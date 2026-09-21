import re
from datetime import datetime, timedelta
from pathlib import Path

from src import store

_ROOT = Path(__file__).resolve().parent.parent
_BLACKLIST = {
    line.strip()
    for line in (_ROOT / "config" / "tickers_blacklist.txt").read_text().splitlines()
    if line.strip()
}

_CASHTAG_RE = re.compile(r'\$([A-Z]{1,5}(?:\.[A-Z])?)\b')


def extract_tickers(tweet_text):
    seen = set()
    result = []
    for ticker in _CASHTAG_RE.findall(tweet_text):
        if ticker in seen or ticker in _BLACKLIST:
            continue
        seen.add(ticker)
        result.append(ticker)
    return result


def _week_start(created_at):
    try:
        dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S +0000 %Y")
    except (ValueError, TypeError):
        return None
    return (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")


def process_new_tweets():
    with store.get_db() as conn:
        rows = conn.execute("""
            SELECT t.id, t.text, t.created_at
            FROM tweets t
            WHERE NOT EXISTS (SELECT 1 FROM mentions m WHERE m.tweet_id = t.id)
        """).fetchall()

    total = 0
    with store.get_db() as conn:
        for row in rows:
            ws = _week_start(row["created_at"])
            if ws is None:
                continue
            for ticker in extract_tickers(row["text"]):
                store.insert_mention(conn, row["id"], ticker, ws)
                total += 1
    return total
