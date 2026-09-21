import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

from src import store, analyze_compute

_ROOT    = Path(__file__).resolve().parent.parent
_cfg     = yaml.safe_load((_ROOT / "config" / "config.yaml").read_text())
_DASH    = _ROOT / _cfg["paths"]["dashboard"]
_TMPL    = Path(__file__).resolve().parent / "templates"
_REPORTS = _ROOT / "reports"

# ── SVG helpers ───────────────────────────────────────────────────────────────
def sparkline(values, w=120, h=32, color="#f59e0b"):
    if not values or max(values, default=0) == 0:
        return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
                f'<line x1="0" y1="{h//2}" x2="{w}" y2="{h//2}" '
                f'stroke="#202026" stroke-width="1"/></svg>')
    mx  = max(values)
    n   = len(values)
    pad = 2
    pts = []
    for i, v in enumerate(values):
        x = pad + i * (w - 2 * pad) / max(n - 1, 1)
        y = h - pad - (v / mx) * (h - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<polyline points="{" ".join(pts)}" fill="none" '
            f'stroke="{color}" stroke-width="1.5" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')


def sent_bar(sent):
    total = sent.get("bullish", 0) + sent.get("bearish", 0) + sent.get("neutral", 0)
    if not total:
        return '<div class="sbar"><div class="sbar-n" style="width:100%"></div></div>'
    bp = sent.get("bullish", 0) / total * 100
    np_ = sent.get("neutral", 0) / total * 100
    rp = 100 - bp - np_
    return (f'<div class="sbar">'
            f'<div class="sbar-b" style="width:{bp:.1f}%"></div>'
            f'<div class="sbar-n" style="width:{np_:.1f}%"></div>'
            f'<div class="sbar-r" style="width:{rp:.1f}%"></div>'
            f'</div>')

# NOTE: the per-ticker / per-author helpers below take an open `conn` rather than
# opening their own. They are called in tight loops (one call per ticker and per
# author); opening a fresh connection each time meant ~1,000+ connects against
# Supabase's pooler per dashboard build — slow, and prone to hanging on pool
# exhaustion. generate_dashboard now opens ONE connection and threads it through.

# ── Sentiment helpers ─────────────────────────────────────────────────────────
def _ticker_sentiment(conn, ticker, week_start, week_end, summaries=None):
    """Return sentiment dict or None if no scored mentions exist."""
    rows = conn.execute("""
        SELECT sentiment, COUNT(*) AS cnt
        FROM mentions
        WHERE ticker = ? AND week_start >= ? AND week_start <= ?
          AND sentiment IS NOT NULL AND sentiment != 'unclear'
        GROUP BY sentiment
    """, (ticker, week_start, week_end)).fetchall()

    if not rows:
        return None

    counts = {r["sentiment"]: r["cnt"] for r in rows}
    result = {
        "bullish": counts.get("bullish", 0),
        "bearish": counts.get("bearish", 0),
        "neutral": counts.get("neutral", 0),
        "bull_text": "",
        "bear_text": "",
    }

    if summaries and ticker in summaries:
        result["bull_text"] = summaries[ticker].get("bull_text", "")
        result["bear_text"] = summaries[ticker].get("bear_text", "")

    return result


def _load_summaries(week_start):
    path = _REPORTS / f"{week_start}_summaries.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


# ── DB helpers ────────────────────────────────────────────────────────────────
def _account_activity(conn, week_start, week_end, limit=30):
    rows = conn.execute("""
        SELECT a.username, COUNT(m.id) AS mention_count
        FROM accounts a
        JOIN tweets t  ON t.user_id  = a.user_id
        JOIN mentions m ON m.tweet_id = t.id
        WHERE m.week_start >= ? AND m.week_start <= ?
        GROUP BY a.user_id
        ORDER BY mention_count DESC
        LIMIT ?
    """, (week_start, week_end, limit)).fetchall()
    return [dict(r) for r in rows]


def _ticker_weekly_history(conn, ticker, current_week_start, weeks=12):
    ws = date.fromisoformat(current_week_start)
    weeks_list = [(ws - timedelta(weeks=i)).isoformat() for i in range(weeks - 1, -1, -1)]
    counts = {
        r["week_start"]: r["cnt"]
        for r in conn.execute("""
            SELECT week_start, COUNT(*) AS cnt FROM mentions
            WHERE ticker = ? AND week_start IN ({})
            GROUP BY week_start
        """.format(",".join("?" * weeks), ), [ticker] + weeks_list).fetchall()
    }
    return [counts.get(w, 0) for w in weeks_list], list(zip(weeks_list, [counts.get(w, 0) for w in weeks_list]))


def _fmt_date(created_at):
    try:
        return datetime.strptime(created_at, "%a %b %d %H:%M:%S +0000 %Y").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return created_at or ""


def _ticker_tweets(conn, ticker, week_start, week_end, limit=50):
    rows = conn.execute("""
        SELECT t.id, t.text, t.created_at, a.username,
               'https://x.com/' || a.username || '/status/' || t.id AS url,
               m.sentiment, m.conviction, m.rationale
        FROM mentions m
        JOIN tweets   t ON t.id       = m.tweet_id
        JOIN accounts a ON a.user_id  = t.user_id
        WHERE m.ticker = ? AND m.week_start >= ? AND m.week_start <= ?
        ORDER BY t.id DESC
        LIMIT ?
    """, (ticker, week_start, week_end, limit)).fetchall()
    return [{**dict(r), "date": _fmt_date(r["created_at"])} for r in rows]


def _ticker_prior_tweets(conn, ticker, before_week_start, limit=100):
    """Tweets mentioning ticker in all weeks before the current one, grouped by week."""
    rows = conn.execute("""
        SELECT m.week_start, t.id, t.text, t.created_at, a.username,
               'https://x.com/' || a.username || '/status/' || t.id AS url
        FROM mentions m
        JOIN tweets   t ON t.id       = m.tweet_id
        JOIN accounts a ON a.user_id  = t.user_id
        WHERE m.ticker = ? AND m.week_start < ?
        ORDER BY m.week_start DESC, t.id DESC
        LIMIT ?
    """, (ticker, before_week_start, limit)).fetchall()

    by_week: dict = {}
    for r in rows:
        w = r["week_start"]
        by_week.setdefault(w, []).append({**dict(r), "date": _fmt_date(r["created_at"])})
    return [(w, by_week[w]) for w in sorted(by_week, reverse=True)]


def _author_info(conn, username):
    row = conn.execute(
        "SELECT * FROM accounts WHERE username = ?", (username,)
    ).fetchone()
    return dict(row) if row else None


def _author_week_mentions(conn, username, week_start, week_end, limit=50):
    rows = conn.execute("""
        SELECT m.ticker, t.text,
               'https://x.com/' || a.username || '/status/' || t.id AS url
        FROM mentions m
        JOIN tweets   t ON t.id      = m.tweet_id
        JOIN accounts a ON a.user_id = t.user_id
        WHERE a.username = ? AND m.week_start >= ? AND m.week_start <= ?
        ORDER BY m.ticker, t.id DESC
        LIMIT ?
    """, (username, week_start, week_end, limit)).fetchall()
    return [dict(r) for r in rows]


def _author_top_tickers(conn, username, limit=10):
    rows = conn.execute("""
        SELECT m.ticker, COUNT(*) AS count
        FROM mentions m
        JOIN tweets   t ON t.id      = m.tweet_id
        JOIN accounts a ON a.user_id = t.user_id
        WHERE a.username = ?
        GROUP BY m.ticker ORDER BY count DESC LIMIT ?
    """, (username, limit)).fetchall()
    return [dict(r) for r in rows]


def _author_weekly_activity(conn, username, current_week_start, weeks=12):
    ws = date.fromisoformat(current_week_start)
    weeks_list = [(ws - timedelta(weeks=i)).isoformat() for i in range(weeks - 1, -1, -1)]
    counts = {
        r["week_start"]: r["cnt"]
        for r in conn.execute("""
            SELECT m.week_start, COUNT(DISTINCT m.id) AS cnt
            FROM mentions m
            JOIN tweets   t ON t.id      = m.tweet_id
            JOIN accounts a ON a.user_id = t.user_id
            WHERE a.username = ? AND m.week_start IN ({})
            GROUP BY m.week_start
        """.format(",".join("?" * weeks)), [username] + weeks_list).fetchall()
    }
    return [counts.get(w, 0) for w in weeks_list]


# ── Write helper ──────────────────────────────────────────────────────────────
def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── Main entry point ──────────────────────────────────────────────────────────
def generate_dashboard(week_start, week_end, new_ticker_data=None):
    env = Environment(loader=FileSystemLoader(str(_TMPL)), autoescape=True)
    env.globals["sparkline"] = sparkline
    env.globals["sent_bar"]  = sent_bar

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ── Shared data (analyze_compute manages its own short connections) ───────
    top   = analyze_compute.top_mentioned(week_start, week_end)
    uniq  = analyze_compute.unique_mentioners(week_start, week_end)
    wow   = analyze_compute.wow_movers(week_start, week_end)
    mom   = analyze_compute.mom_movers(week_start, week_end)
    new   = analyze_compute.new_tickers(week_start, week_end)

    summaries       = _load_summaries(week_start)
    uniq_map        = {r["ticker"]: r["mentioners"] for r in uniq}
    top_tickers_set = {r["ticker"] for r in top}

    # One connection for the whole build — the per-ticker/per-author helpers
    # below reuse it instead of each opening their own (see note above).
    with store.get_db() as conn:
        activity = _account_activity(conn, week_start, week_end)

        diverge = [
            {"ticker": t, "sent": s}
            for t in top_tickers_set
            if (s := _ticker_sentiment(conn, t, week_start, week_end, summaries))
            and (s["bullish"] + s["bearish"] + s["neutral"]) > 0
        ]

        total_tweets    = conn.execute("SELECT COUNT(*) AS n FROM tweets").fetchone()["n"]
        total_mentions  = conn.execute("SELECT COUNT(*) AS n FROM mentions").fetchone()["n"]
        active_accounts = conn.execute("SELECT COUNT(*) AS n FROM accounts WHERE active=1").fetchone()["n"]

        # ── index.html ───────────────────────────────────────────────────────
        idx_ctx = dict(
            week_start=week_start, week_end=week_end, generated=generated,
            top=top, uniq_map=uniq_map, wow=wow, mom=mom,
            new_tickers=new, new_ticker_summaries=new_ticker_data or [], diverge=diverge, activity=activity,
            total_tweets=total_tweets, total_mentions=total_mentions,
            active_accounts=active_accounts, base="",
        )
        _write(_DASH / "index.html", env.get_template("index.html").render(**idx_ctx))

        # ── ticker pages — every ticker linked from the dashboard ────────────
        all_tickers = (
            {r["ticker"] for r in top}
            | {r["ticker"] for r in wow}
            | {r["ticker"] for r in mom}
            | set(new)
        )
        # only generate pages for tickers that actually have mentions
        has_mentions = {
            r["ticker"]
            for r in conn.execute("SELECT DISTINCT ticker FROM mentions").fetchall()
        }
        all_tickers &= has_mentions

        for ticker in all_tickers:
            history_vals, history_weeks = _ticker_weekly_history(conn, ticker, week_start)
            tweets       = _ticker_tweets(conn, ticker, week_start, week_end)
            prior_tweets = _ticker_prior_tweets(conn, ticker, week_start)
            sent         = _ticker_sentiment(conn, ticker, week_start, week_end, summaries)
            _write(
                _DASH / "tickers" / f"{ticker}.html",
                env.get_template("ticker.html").render(
                    ticker=ticker, week_start=week_start, week_end=week_end,
                    generated=generated, history=history_vals,
                    history_weeks=history_weeks, tweets=tweets,
                    prior_tweets=prior_tweets,
                    sent=sent, base="../",
                )
            )

        # ── author pages — every author who has mentions this week ───────────
        all_authors = [
            r["username"]
            for r in conn.execute("""
                SELECT DISTINCT a.username
                FROM accounts a
                JOIN tweets   t ON t.user_id  = a.user_id
                JOIN mentions m ON m.tweet_id = t.id
                WHERE m.week_start >= ? AND m.week_start <= ?
            """, (week_start, week_end)).fetchall()
        ]
        for username in all_authors:
            info = _author_info(conn, username)
            if not info:
                continue
            week_mentions = _author_week_mentions(conn, username, week_start, week_end)
            top_tickers   = _author_top_tickers(conn, username)
            act_history   = _author_weekly_activity(conn, username, week_start)
            _write(
                _DASH / "authors" / f"{username}.html",
                env.get_template("author.html").render(
                    account=info, week_start=week_start, week_end=week_end,
                    generated=generated, week_mentions=week_mentions,
                    top_tickers=top_tickers, activity_history=act_history, base="../",
                )
            )

    return str(_DASH / "index.html")
