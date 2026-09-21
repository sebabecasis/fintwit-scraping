import math
from collections import defaultdict
from datetime import date, timedelta

from src import store


def _d(s):
    return date.fromisoformat(s)


def top_mentioned(week_start, week_end, limit=20):
    with store.get_db() as conn:
        rows = conn.execute("""
            SELECT ticker, COUNT(*) AS count
            FROM mentions
            WHERE week_start >= ? AND week_start <= ?
            GROUP BY ticker
            ORDER BY count DESC
            LIMIT ?
        """, (week_start, week_end, limit)).fetchall()
    return [{"ticker": r["ticker"], "count": r["count"]} for r in rows]


def unique_mentioners(week_start, week_end, limit=20):
    with store.get_db() as conn:
        rows = conn.execute("""
            SELECT m.ticker, COUNT(DISTINCT t.user_id) AS mentioners
            FROM mentions m
            JOIN tweets t ON t.id = m.tweet_id
            WHERE m.week_start >= ? AND m.week_start <= ?
            GROUP BY m.ticker
            ORDER BY mentioners DESC
            LIMIT ?
        """, (week_start, week_end, limit)).fetchall()
    return [{"ticker": r["ticker"], "mentioners": r["mentioners"]} for r in rows]


def wow_movers(week_start, week_end, limit=10):
    prior_start = (_d(week_start) - timedelta(days=7)).isoformat()
    prior_end   = (_d(week_end)   - timedelta(days=7)).isoformat()

    with store.get_db() as conn:
        this_rows  = conn.execute("""
            SELECT ticker, COUNT(*) AS count FROM mentions
            WHERE week_start >= ? AND week_start <= ?
            GROUP BY ticker
        """, (week_start, week_end)).fetchall()
        prior_rows = conn.execute("""
            SELECT ticker, COUNT(*) AS count FROM mentions
            WHERE week_start >= ? AND week_start <= ?
            GROUP BY ticker
        """, (prior_start, prior_end)).fetchall()

    this_map  = {r["ticker"]: r["count"] for r in this_rows}
    prior_map = {r["ticker"]: r["count"] for r in prior_rows}

    results = []
    for ticker in set(this_map) | set(prior_map):
        tw = this_map.get(ticker, 0)
        pw = prior_map.get(ticker, 0)
        abs_change = tw - pw
        pct_change = round((tw - pw) / pw * 100, 1) if pw else None
        results.append({
            "ticker": ticker,
            "this_week": tw,
            "prior_week": pw,
            "abs_change": abs_change,
            "pct_change": pct_change,
            "low_signal": pw < 5,
        })

    results.sort(key=lambda x: abs(x["abs_change"]), reverse=True)
    return results[:limit]


def mom_movers(week_start, week_end, limit=10):
    # current window = [week_start, week_end]; prior window = same length shifted back 28 days
    current_start = week_start
    current_end   = week_end
    prior_start   = (_d(week_start) - timedelta(days=28)).isoformat()
    prior_end     = (_d(week_end)   - timedelta(days=28)).isoformat()

    with store.get_db() as conn:
        current_rows = conn.execute("""
            SELECT ticker, COUNT(*) AS count FROM mentions
            WHERE week_start >= ? AND week_start <= ?
            GROUP BY ticker
        """, (current_start, current_end)).fetchall()
        prior_rows = conn.execute("""
            SELECT ticker, COUNT(*) AS count FROM mentions
            WHERE week_start >= ? AND week_start <= ?
            GROUP BY ticker
        """, (prior_start, prior_end)).fetchall()

    cur_map   = {r["ticker"]: r["count"] for r in current_rows}
    prior_map = {r["ticker"]: r["count"] for r in prior_rows}

    results = []
    for ticker in set(cur_map) | set(prior_map):
        cw = cur_map.get(ticker, 0)
        pw = prior_map.get(ticker, 0)
        abs_change = cw - pw
        pct_change = round((cw - pw) / pw * 100, 1) if pw else None
        results.append({
            "ticker": ticker,
            "current_4w": cw,
            "prior_4w": pw,
            "abs_change": abs_change,
            "pct_change": pct_change,
        })

    results.sort(key=lambda x: abs(x["abs_change"]), reverse=True)
    return results[:limit]


def new_tickers(week_start, week_end, lookback_weeks=8):
    lookback_start = (_d(week_start) - timedelta(weeks=lookback_weeks)).isoformat()

    with store.get_db() as conn:
        this_week = {r["ticker"] for r in conn.execute("""
            SELECT DISTINCT ticker FROM mentions
            WHERE week_start >= ? AND week_start <= ?
        """, (week_start, week_end)).fetchall()}

        prior = {r["ticker"] for r in conn.execute("""
            SELECT DISTINCT ticker FROM mentions
            WHERE week_start >= ? AND week_start < ?
        """, (lookback_start, week_start)).fetchall()}

    return sorted(this_week - prior)


def divergence_candidates(week_start, week_end, limit=10):
    sentiment_score = {"bullish": 1, "bearish": -1, "neutral": 0}

    with store.get_db() as conn:
        rows = conn.execute("""
            SELECT ticker, sentiment, conviction
            FROM mentions
            WHERE week_start >= ? AND week_start <= ?
              AND sentiment IS NOT NULL
              AND LOWER(sentiment) != 'unclear'
        """, (week_start, week_end)).fetchall()

    by_ticker = defaultdict(list)
    for row in rows:
        score = sentiment_score.get(row["sentiment"])
        if score is None:
            continue
        conviction = row["conviction"] or 1
        by_ticker[row["ticker"]].append(score * conviction)

    results = []
    for ticker, scores in by_ticker.items():
        if len(scores) < 5:
            continue
        mean = sum(scores) / len(scores)
        stdev = math.sqrt(sum((s - mean) ** 2 for s in scores) / len(scores))
        results.append({
            "ticker": ticker,
            "stdev": round(stdev, 3),
            "mention_count": len(scores),
            "mean_score": round(mean, 3),
        })

    results.sort(key=lambda x: x["stdev"], reverse=True)
    return results[:limit]
