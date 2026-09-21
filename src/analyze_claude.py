import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from src import store, analyze_compute

load_dotenv()

_ROOT       = Path(__file__).resolve().parent.parent
_REPORTS    = _ROOT / "reports"
_HAIKU      = "claude-haiku-4-5-20251001"
_SONNET     = "claude-sonnet-4-6"

_THEME_SYSTEM = """\
You are a financial markets analyst reading fintwit (financial Twitter). Extract every distinct \
investment theme present in the tweets. Be specific and tight — prefer "Edge AI inference chips" \
over "AI", "GLP-1 obesity drugs" over "biotech", "small modular reactors" over "energy". \
Include macro themes (e.g. "US-China tariffs", "Fed rate pause"), sector themes \
(e.g. "defence primes", "uranium miners"), and catalyst-driven themes \
(e.g. "Nvidia earnings beat", "Bitcoin ETF inflows"). \
For each theme also list every stock ticker symbol mentioned in tweets related to that theme, \
and every @username (without the @) who tweeted about it. \
Respond with ONLY a raw JSON array of objects — no markdown, no explanation. \
Each object must have exactly three keys: "theme" (string), "tickers" (array of uppercase strings), \
and "accounts" (array of username strings). \
Start with [ and end with ].
"""

_SCORE_SYSTEM = """\
You are a financial tweet sentiment classifier. Respond with ONLY a raw JSON array — \
no markdown, no code fences, no explanation text before or after. Start your response with [ \
and end with ].

Each element must have EXACTLY these four keys (no others):
  "mention_id": the integer given in the input
  "sentiment":  MUST be one of exactly: "bullish", "bearish", "neutral", "unclear"
  "conviction": integer 1-5 (1=weak, 5=strong; use 3 for neutral)
  "rationale":  8-12 word string, specific to the tweet

Definitions:
  bullish  = clear positive view on that ticker
  bearish  = clear negative view on that ticker
  neutral  = informational, no clear direction
  unclear  = direction cannot be determined
"""


def _strip_fences(text):
    """Strip markdown code fences that models sometimes add despite instructions."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
    if t.endswith("```"):
        t = t.rsplit("```", 1)[0]
    return t.strip()


def _client():
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _save_themes(label, themes):
    with store.get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS themes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                label        TEXT NOT NULL,
                theme        TEXT NOT NULL,
                narrative    TEXT,
                tickers      TEXT,
                accounts     TEXT,
                extracted_at TEXT NOT NULL
            )
        """)
        conn.execute("DELETE FROM themes WHERE label = ?", (label,))
        now = datetime.now(timezone.utc).isoformat()
        for entry in themes:
            conn.execute("""
                INSERT INTO themes (label, theme, narrative, tickers, accounts, extracted_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                label,
                entry.get("theme", ""),
                entry.get("narrative", ""),
                ", ".join(entry.get("tickers") or []),
                ", ".join(entry.get("accounts") or []),
                now,
            ))


def _load_known_themes():
    """Return list of distinct theme names ever saved to the DB."""
    with store.get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS themes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL, theme TEXT NOT NULL,
                narrative TEXT, tickers TEXT, accounts TEXT,
                extracted_at TEXT NOT NULL
            )
        """)
        rows = conn.execute(
            "SELECT DISTINCT theme FROM themes ORDER BY theme"
        ).fetchall()
    return [r["theme"] for r in rows]


def compare_themes(new_themes):
    """
    Compare new_themes (list of {theme,narrative,tickers,accounts}) against
    known themes in DB. Returns:
      matched: list of {new_theme, existing_theme} — new maps to a known theme
      novel:   list of {theme,narrative,tickers,accounts} — genuinely new
    """
    known = _load_known_themes()
    if not known:
        return [], new_themes  # nothing to compare against, all are novel

    new_names = [t["theme"] for t in new_themes]
    prompt = f"""\
You are comparing a new list of investment themes against a known theme library.

KNOWN THEMES:
{json.dumps(known)}

NEW THEMES FROM THIS RUN:
{json.dumps(new_names)}

For each new theme decide:
- "matched": it is the same as or a near-duplicate of a known theme (merge it in)
- "novel": it is genuinely new and not covered by any known theme

Respond with ONLY valid JSON — no markdown, no explanation:
{{"matched": [{{"new": "...", "existing": "..."}}], "novel": ["..."]}}"""

    client = _client()
    msg = client.messages.create(
        model=_SONNET,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = _strip_fences(msg.content[0].text)
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, IndexError) as e:
        print(f"compare_themes parse failed — {e}\n{raw[:300]}")
        return [], new_themes

    matched = result.get("matched", [])
    novel_names = set(result.get("novel", []))
    novel = [t for t in new_themes if t["theme"] in novel_names]
    return matched, novel


def _chunk_tweets(rows, max_chars=10_000):
    batch, size = [], 0
    for r in rows:
        n = len(r["text"])
        if batch and size + n > max_chars:
            yield batch
            batch, size = [], 0
        batch.append(r)
        size += n
    if batch:
        yield batch


# ── score_mentions ────────────────────────────────────────────────────────────

def score_mentions(week_start, week_end):
    """
    Score unscored mentions in batches of 50. Idempotent — skips already-scored rows.
    Returns (scored_count, estimated_cost_usd).
    """
    with store.get_db() as conn:
        rows = conn.execute("""
            SELECT m.id, m.tweet_id, m.ticker, t.text
            FROM mentions m
            JOIN tweets t ON t.id = m.tweet_id
            WHERE m.week_start >= ? AND m.week_start <= ?
              AND m.scored_at IS NULL
            ORDER BY m.id
        """, (week_start, week_end)).fetchall()

    if not rows:
        print("No unscored mentions found.")
        return 0, 0.0

    client      = _client()
    total       = 0
    in_tokens   = 0
    out_tokens  = 0
    BATCH       = 50

    for batch_start in range(0, len(rows), BATCH):
        batch = rows[batch_start:batch_start + BATCH]

        user_content = "\n\n".join(
            f'mention_id={row["id"]}\nticker=${row["ticker"]}\ntweet: {row["text"][:280]}'
            for row in batch
        )

        msg = client.messages.create(
            model=_HAIKU,
            max_tokens=4096,
            system=_SCORE_SYSTEM,
            messages=[
                {"role": "user",      "content":
                    f"Score these {len(batch)} mentions:\n\n{user_content}"},
                {"role": "assistant", "content": "["},   # prefill forces raw JSON array
            ],
        )

        in_tokens  += msg.usage.input_tokens
        out_tokens += msg.usage.output_tokens

        raw = "[" + msg.content[0].text  # prepend the prefill back
        try:
            scores = json.loads(_strip_fences(raw))
        except (json.JSONDecodeError, IndexError):
            print(f"  [WARN] JSON parse failed for batch {batch_start//BATCH + 1} — skipping")
            continue

        now     = datetime.now(timezone.utc).isoformat()
        id_map  = {row["id"]: row for row in batch}

        with store.get_db() as conn:
            for s in scores:
                mid = s.get("mention_id")
                if mid not in id_map:
                    continue
                conn.execute("""
                    UPDATE mentions
                    SET sentiment=?, conviction=?, rationale=?, scored_at=?
                    WHERE id=?
                """, (s.get("sentiment"), s.get("conviction"),
                      s.get("rationale"), now, mid))

        total += len(batch)
        print(f"  scored batch {batch_start//BATCH + 1}/{-(-len(rows)//BATCH)} "
              f"({total}/{len(rows)})")

    # Haiku pricing: $0.80/M input, $4.00/M output
    cost = (in_tokens / 1_000_000 * 0.80) + (out_tokens / 1_000_000 * 4.00)
    return total, cost


# ── divergence_summaries ──────────────────────────────────────────────────────

def divergence_summaries(week_start, week_end):
    """
    For the top 10 divergence candidates, ask Claude for a 2-sentence bull case
    and 2-sentence bear case per ticker. Writes results to reports/{week_start}_summaries.json.
    Returns dict {ticker: {bull_text, bear_text}}.
    """
    candidates = analyze_compute.divergence_candidates(week_start, week_end, limit=10)
    if not candidates:
        print("No divergence candidates (need ≥5 scored mentions per ticker).")
        return {}

    client  = _client()
    results = {}

    for c in candidates:
        ticker = c["ticker"]

        with store.get_db() as conn:
            tweets = conn.execute("""
                SELECT t.text, m.sentiment, m.conviction, m.rationale
                FROM mentions m
                JOIN tweets t ON t.id = m.tweet_id
                WHERE m.ticker = ? AND m.week_start >= ? AND m.week_start <= ?
                  AND m.sentiment IS NOT NULL AND m.sentiment != 'unclear'
                ORDER BY m.conviction DESC
                LIMIT 20
            """, (ticker, week_start, week_end)).fetchall()

        if not tweets:
            continue

        tweet_block = "\n".join(
            f'- [{t["sentiment"].upper()} conv={t["conviction"]}] {t["text"][:200]}'
            for t in tweets
        )

        msg = client.messages.create(
            model=_SONNET,
            max_tokens=300,
            messages=[{"role": "user", "content":
                f"""These are fintwit tweets about ${ticker} this week (sentiment classified):

{tweet_block}

Write exactly 2 sentences for the bull case and exactly 2 sentences for the bear case.
Be specific — cite actual catalysts or risks mentioned. No hedging, no platitudes.

Output ONLY valid JSON:
{{"bull_text": "...", "bear_text": "..."}}"""}]
        )

        try:
            parsed = json.loads(msg.content[0].text)
            results[ticker] = {
                "bull_text": parsed.get("bull_text", ""),
                "bear_text": parsed.get("bear_text", ""),
                "stdev":     c["stdev"],
                "mentions":  c["mention_count"],
            }
        except (json.JSONDecodeError, IndexError):
            pass

    _REPORTS.mkdir(parents=True, exist_ok=True)
    out_path = _REPORTS / f"{week_start}_summaries.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"Summaries written to {out_path}")
    return results


# ── weekly_narrative ──────────────────────────────────────────────────────────

def weekly_narrative(week_start, week_end, metrics):
    """
    Generate a 1-paragraph editorial from the week's metrics.
    Returns the narrative string.
    """
    top   = metrics.get("top",  [])[:10]
    wow   = metrics.get("wow",  [])[:5]
    new   = metrics.get("new",  [])

    top_txt = "\n".join(f'  ${r["ticker"]}: {r["count"]} mentions' for r in top)
    wow_txt = "\n".join(
        f'  ${r["ticker"]}: {r["abs_change"]:+d} WoW '
        f'({"+" if r["abs_change"]>0 else ""}{r.get("pct_change") or "new"}%)'
        for r in wow
    )
    new_txt = ", ".join(f"${t}" for t in new) if new else "none"

    prompt = f"""Week {week_start} → {week_end} fintwit data:

TOP MENTIONED:
{top_txt}

WOW MOVERS:
{wow_txt}

NEW TICKERS: {new_txt}

Write one tight paragraph (4–6 sentences) of editorial commentary on what this week's \
fintwit data signals. Be specific — name tickers, name the narrative shifts. \
No hedging phrases ("it remains to be seen", "time will tell"). \
No generic statements about volatility or markets in general. \
Write as a sharp analyst giving a Monday-morning briefing."""

    client = _client()
    msg    = client.messages.create(
        model=_SONNET,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


# ── new_ticker_summaries ──────────────────────────────────────────────────────

def new_ticker_summaries(week_start, week_end, min_mentions=3, top_n=30):
    """
    For new tickers this week (not seen in prior 8 weeks), classify each into a
    theme + sub-theme using tweet content. Returns list of:
      {ticker, theme, sub_theme, accounts, tweet_count}
    Filtered to tickers with >= min_mentions, capped at top_n by tweet count.
    """
    new = analyze_compute.new_tickers(week_start, week_end)
    if not new:
        return []

    with store.get_db() as conn:
        ticker_data = []
        for ticker in new:
            row = conn.execute("""
                SELECT COUNT(*) as tweet_count,
                       COUNT(DISTINCT a.username) as account_count,
                       STRING_AGG(DISTINCT a.username, ',') as accounts
                FROM mentions m
                JOIN tweets t ON m.tweet_id = t.id
                JOIN accounts a ON t.user_id = a.user_id
                WHERE m.ticker = ? AND m.week_start >= ? AND m.week_start <= ?
            """, (ticker, week_start, week_end)).fetchone()

            if row["tweet_count"] < min_mentions:
                continue

            samples = conn.execute("""
                SELECT t.text, m.rationale
                FROM mentions m
                JOIN tweets t ON m.tweet_id = t.id
                WHERE m.ticker = ? AND m.week_start >= ? AND m.week_start <= ?
                ORDER BY COALESCE(m.conviction, 0) DESC
                LIMIT 5
            """, (ticker, week_start, week_end)).fetchall()

            ticker_data.append({
                "ticker": ticker,
                "tweet_count": row["tweet_count"],
                "accounts": [a for a in (row["accounts"] or "").split(",") if a],
                "samples": [s["text"][:200] for s in samples],
                "rationales": [s["rationale"] for s in samples if s["rationale"]],
            })

    ticker_data.sort(key=lambda x: x["tweet_count"], reverse=True)
    ticker_data = ticker_data[:top_n]

    if not ticker_data:
        return []

    ticker_blocks = []
    for td in ticker_data:
        block = f'${td["ticker"]} — {td["tweet_count"]} tweets from {len(td["accounts"])} accounts\n'
        for s in td["samples"][:3]:
            block += f'  · {s}\n'
        ticker_blocks.append(block)

    prompt = f"""You are classifying new stock tickers that appeared on fintwit (financial Twitter) this week.
For each ticker, classify it into a theme and sub-theme based on how the network is characterising it in the tweets shown.

Be specific:
- theme: broad category (e.g. "AI Infrastructure", "Semiconductors", "Biotech", "Energy", "Macro ETF", "Defence")
- sub_theme: tight specific angle (e.g. "Edge AI inference chips", "NAND memory", "GLP-1 obesity drugs", "Small modular reactors", "Rate expectations")

Tickers:

{''.join(ticker_blocks)}

Respond ONLY with a raw JSON array — no markdown, no explanation.
Each object must have exactly: "ticker" (string), "theme" (string), "sub_theme" (string).
Start with [ and end with ]."""

    client = _client()
    msg = client.messages.create(
        model=_SONNET,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = _strip_fences(msg.content[0].text)
    try:
        classifications = json.loads(raw)
    except (json.JSONDecodeError, IndexError) as e:
        print(f"  [WARN] new_ticker_summaries parse failed — {e}")
        classifications = []

    class_map = {c["ticker"]: c for c in classifications}
    results = []
    for td in ticker_data:
        c = class_map.get(td["ticker"], {})
        results.append({
            "ticker": td["ticker"],
            "theme": c.get("theme", "Unknown"),
            "sub_theme": c.get("sub_theme", ""),
            "accounts": td["accounts"],
            "tweet_count": td["tweet_count"],
            "rationales": td["rationales"],
        })

    return results


# ── extract_themes ────────────────────────────────────────────────────────────

def extract_themes(since=None, until=None, label="themes"):
    """
    Read tweets from the DB (optionally filtered to [since, until)),
    extract tight investment themes via Claude, consolidate across batches,
    write to reports/{label}.txt. Returns list of theme strings.
    """
    with store.get_db() as conn:
        if since and until:
            rows = conn.execute(
                """SELECT t.id, t.text, a.username
                   FROM tweets t JOIN accounts a ON a.user_id = t.user_id
                   WHERE t.created_at >= ? AND t.created_at < ?
                   ORDER BY t.created_at""",
                (since, until),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT t.id, t.text, a.username
                   FROM tweets t JOIN accounts a ON a.user_id = t.user_id
                   ORDER BY t.created_at"""
            ).fetchall()

    if not rows:
        print("No tweets in DB.")
        return []

    client  = _client()
    batches = list(_chunk_tweets(rows))
    # raw_themes: list of {theme, tickers[]} dicts across all batches
    raw_themes: list[dict] = []

    print(f"Extracting themes from {len(rows)} tweets across {len(batches)} batches…")

    for i, batch in enumerate(batches, 1):
        payload = "\n---\n".join(f'@{r["username"]}: {r["text"]}' for r in batch)
        msg = client.messages.create(
            model=_SONNET,
            max_tokens=8096,
            system=_THEME_SYSTEM,
            messages=[{"role": "user", "content": payload}],
        )
        raw_text = msg.content[0].text if msg.content else ""
        if msg.stop_reason == "max_tokens":
            print(f"  batch {i}/{len(batches)}: HIT TOKEN LIMIT — response truncated ({len(raw_text)} chars), skipping")
            continue
        try:
            themes = json.loads(_strip_fences(raw_text))
            raw_themes.extend(themes)
            print(f"  batch {i}/{len(batches)}: {len(themes)} themes")
        except (json.JSONDecodeError, IndexError) as e:
            print(f"  batch {i}/{len(batches)}: parse failed — {e}")
            print(f"  raw response ({len(raw_text)} chars):\n{raw_text[:500]}\n")

    if not raw_themes:
        print("No themes extracted.")
        return []

    # consolidation pass — dedup, merge, add narratives
    print(f"Consolidating {len(raw_themes)} raw themes…")
    consolidation_prompt = f"""\
Here are investment themes extracted in batches from fintwit tweets. \
There will be duplicates and near-duplicates. \
Consolidate into a clean final list:
- Merge near-duplicates into one entry, combining their tickers
- Drop anything too generic (e.g. "stocks", "markets", "investing")
- Keep themes specific and tight
- Sort roughly by how broadly represented they appear
- For each final theme, write a 1-2 sentence narrative: what is the crowd saying? \
  Be specific — name catalysts, name the debate. No hedging.

Respond with ONLY a raw JSON array of objects — no markdown, no explanation. \
Each object must have exactly four keys:
  "theme": string
  "narrative": string (1-2 sentences)
  "tickers": array of uppercase ticker strings (deduplicated, sorted)
  "accounts": array of username strings (deduplicated, sorted)

Input:
{json.dumps(raw_themes)}"""

    msg = client.messages.create(
        model=_SONNET,
        max_tokens=4096,
        messages=[{"role": "user", "content": consolidation_prompt}],
    )

    raw_text = msg.content[0].text if msg.content else ""
    try:
        final = json.loads(_strip_fences(raw_text))
    except (json.JSONDecodeError, IndexError) as e:
        print(f"Consolidation parse failed — {e}")
        print(f"Raw response ({len(raw_text)} chars):\n{raw_text[:500]}\n")
        print("Falling back to raw theme names only.")
        final = [{"theme": t.get("theme", t) if isinstance(t, dict) else t,
                  "narrative": "", "tickers": []} for t in raw_themes]

    _REPORTS.mkdir(parents=True, exist_ok=True)
    out_path = _REPORTS / f"{label}.txt"
    lines = []
    for entry in final:
        lines.append(entry.get("theme", ""))
        if entry.get("narrative"):
            lines.append(f"  {entry['narrative']}")
        if entry.get("tickers"):
            lines.append(f"  Tickers: {', '.join(entry['tickers'])}")
        if entry.get("accounts"):
            lines.append(f"  Accounts: {', '.join('@' + a for a in entry['accounts'])}")
        lines.append("")
    out_path.write_text("\n".join(lines))
    _save_themes(label, final)
    print(f"\n{len(final)} themes written to {out_path} and saved to DB")
    return final
