#!/usr/bin/env python3
"""
Finds all active accounts with zero tweets in the DB, writes handles/no_tweets.txt,
then fetches their last 7 days of tweets and extracts ticker mentions.

Usage: python3 no_tweets.py
"""

import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

from src import store
from src.extract import process_new_tweets

load_dotenv()

_ROOT = Path(__file__).resolve().parent
_cfg = yaml.safe_load((_ROOT / "config" / "config.yaml").read_text())
_SOFT_CAP = int(_cfg.get("soft_cap_pages", 5))
_API_BASE = "https://api.getxapi.com"
_OUT = _ROOT / "handles" / "no_tweets.txt"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
_log = logging.getLogger(__name__)


class CreditsExhausted(Exception):
    pass


def _notify(title, message):
    print(f"\n{'!'*60}", flush=True)
    print(f"  {title}", flush=True)
    print(f"  {message}", flush=True)
    print(f"{'!'*60}\n", flush=True)
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{message}" with title "{title}" sound name "Sosumi"'],
            check=False, capture_output=True,
        )
    except FileNotFoundError:
        pass


def _headers():
    key = os.getenv("GETXAPI_KEY")
    if not key:
        raise RuntimeError("GETXAPI_KEY not set")
    return {"Authorization": f"Bearer {key}"}


def _get(path, params, _retries=4, _backoff=5):
    for attempt in range(_retries):
        resp = requests.get(f"{_API_BASE}{path}", headers=_headers(),
                            params=params, timeout=15)

        if resp.status_code == 402:
            _notify("GetXAPI credits exhausted", "Top up at getxapi.com — scrape stopped.")
            raise CreditsExhausted("HTTP 402 — GetXAPI credits exhausted")

        if resp.status_code in (500, 502, 503, 504):
            wait = _backoff * (2 ** attempt)
            _log.warning("HTTP %d (attempt %d/%d) — retrying in %ds",
                         resp.status_code, attempt + 1, _retries, wait)
            if attempt + 1 < _retries:
                time.sleep(wait)
                continue
            resp.raise_for_status()

        if resp.status_code == 200:
            try:
                body = resp.json()
                err = str(body.get("error") or body.get("message") or "").lower()
                if "credit" in err or "quota" in err or "limit" in err:
                    _notify("GetXAPI credits exhausted",
                            f"API returned: {err[:120]} — scrape stopped.")
                    raise CreditsExhausted(f"Credit error in response: {err}")
                return body
            except CreditsExhausted:
                raise
            except Exception:
                pass

        resp.raise_for_status()
        return resp.json()


def fetch_for_accounts(usernames, since, until):
    total = 0
    for username in usernames:
        with store.get_db() as conn:
            row = conn.execute(
                "SELECT user_id FROM accounts WHERE username = ?", (username,)
            ).fetchone()
        if not row:
            _log.warning("@%s not found in DB, skipping", username)
            continue
        user_id = row["user_id"]

        page = 0
        cursor = None
        account_total = 0

        while True:
            if page >= _SOFT_CAP:
                _log.warning("soft cap (%d pages) hit for @%s", _SOFT_CAP, username)
                break

            params = {
                "q": f"from:{username} -filter:replies since:{since} until:{until}",
                "product": "Latest",
            }
            if cursor:
                params["cursor"] = cursor

            data = _get("/twitter/tweet/advanced_search", params)
            tweets = data.get("tweets") or []

            with store.get_db() as conn:
                if tweets and page == 0:
                    author = tweets[0].get("author") or {}
                    if author:
                        store.upsert_account(
                            conn,
                            user_id=user_id,
                            username=author.get("userName") or username,
                            display_name=author.get("name"),
                            followers=author.get("followers"),
                            following=author.get("following"),
                            is_blue_verified=int(bool(author.get("isBlueVerified"))),
                        )

                for t in tweets:
                    store.upsert_tweet(
                        conn,
                        tweet_id=t["id"],
                        user_id=user_id,
                        text=t.get("text", ""),
                        created_at=t.get("createdAt", ""),
                        like_count=t.get("likeCount", 0),
                        retweet_count=t.get("retweetCount", 0),
                        reply_count=t.get("replyCount", 0),
                        view_count=t.get("viewCount", 0),
                    )
                    account_total += 1

            page += 1
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
            time.sleep(0.5)

        _log.info("  @%-25s %d tweets (%d pages)", username, account_total, page)
        total += account_total
        time.sleep(1)

    return total


def main():
    with store.get_db() as conn:
        rows = conn.execute("""
            SELECT username
            FROM accounts
            WHERE active = 1
              AND user_id NOT IN (SELECT DISTINCT user_id FROM tweets)
            ORDER BY username
        """).fetchall()

    handles = [r["username"] for r in rows]

    _OUT.parent.mkdir(exist_ok=True)
    _OUT.write_text("\n".join(handles) + ("\n" if handles else ""))
    _log.info("%d accounts with no tweets → %s", len(handles), _OUT)

    if not handles:
        _log.info("Nothing to fetch.")
        return

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    until = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    _log.info("Fetching %s → %s", since, until)

    tweets_fetched = fetch_for_accounts(handles, since, until)
    _log.info("Total tweets fetched: %d", tweets_fetched)

    mentions = process_new_tweets()
    _log.info("Ticker mentions extracted: %d", mentions)


if __name__ == "__main__":
    try:
        main()
    except CreditsExhausted as e:
        _log.error("%s", e)
        sys.exit(1)
