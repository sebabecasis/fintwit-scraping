import logging
import os
import time
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

from src import store

load_dotenv()

_ROOT = Path(__file__).resolve().parent.parent
_cfg = yaml.safe_load((_ROOT / "config" / "config.yaml").read_text())
_SOFT_CAP = int(_cfg.get("soft_cap_pages", 5))
_API_BASE = "https://api.getxapi.com"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
_log = logging.getLogger(__name__)


def _headers():
    key = os.getenv("GETXAPI_KEY")
    if not key:
        raise RuntimeError("GETXAPI_KEY not set")
    return {"Authorization": f"Bearer {key}"}


def _get(path, params):
    resp = requests.get(f"{_API_BASE}{path}", headers=_headers(),
                        params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ── sync_roster ───────────────────────────────────────────────────────────────

def sync_roster():
    """Fetch all list members, upsert into accounts. Returns (added, unchanged)."""
    list_id = os.getenv("X_LIST_ID")
    if not list_id:
        raise RuntimeError("X_LIST_ID not set")
    # accept full URL (https://x.com/i/lists/123) or bare numeric ID
    list_id = list_id.rstrip("/").split("/")[-1]

    with store.get_db() as conn:
        existing_ids = {r["user_id"]
                        for r in conn.execute("SELECT user_id FROM accounts")}

    added = unchanged = 0
    cursor = None

    while True:
        params = {"listId": list_id}
        if cursor:
            params["cursor"] = cursor

        data = _get("/twitter/list/members", params)
        members = data.get("members") or []

        with store.get_db() as conn:
            for m in members:
                user_id = m.get("id")
                username = m.get("userName")
                if not user_id or not username:
                    continue

                store.upsert_account(
                    conn,
                    user_id=user_id,
                    username=username,
                    display_name=m.get("name"),
                    followers=m.get("followers"),
                    following=m.get("following"),
                    is_blue_verified=int(bool(m.get("isBlueVerified"))),
                    source="list",
                )

                if user_id in existing_ids:
                    unchanged += 1
                else:
                    added += 1
                    store.log_roster_change(conn, username, "added", "list sync")
                    existing_ids.add(user_id)

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return added, unchanged


# ── fetch_following_new ───────────────────────────────────────────────────────

def fetch_following_new():
    """Return list of handles in X_USERNAME's following that aren't in accounts yet."""
    username = os.getenv("X_USERNAME")
    if not username:
        raise RuntimeError("X_USERNAME not set")

    with store.get_db() as conn:
        existing = {r["username"].lower()
                    for r in conn.execute("SELECT username FROM accounts")}

    new_handles = []
    cursor = None

    while True:
        params = {"userName": username}
        if cursor:
            params["cursor"] = cursor

        data = _get("/twitter/user/following", params)
        following = data.get("following") or []

        for u in following:
            handle = u.get("userName")
            if handle and handle.lower() not in existing:
                new_handles.append(handle)

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return new_handles


# ── fetch_tweets ──────────────────────────────────────────────────────────────

def fetch_tweets(since, until):
    """
    For each active account, fetch tweets in [since, until] via advanced_search,
    paginating up to soft_cap_pages. Updates account profile from author payload.
    Returns total tweets fetched.
    """
    with store.get_db() as conn:
        accounts = conn.execute(
            "SELECT user_id, username FROM accounts WHERE active = 1 AND source = 'list'"
        ).fetchall()

    total = 0

    for account in accounts:
        user_id = account["user_id"]
        username = account["username"]
        page = 0
        cursor = None

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

            try:
                data = _get("/twitter/tweet/advanced_search", params)
            except requests.exceptions.RequestException as e:
                # Credit/auth rejections (401/402/403/429) must NOT be silently
                # skipped — re-raise so run_weekly's handler fires a credit-alert
                # email. Transient errors (timeouts, 5xx) warn-and-skip as before.
                resp = getattr(e, "response", None)
                if resp is not None and resp.status_code in (401, 402, 403, 429):
                    raise
                _log.warning("request error for @%s (page %d): %s — skipping", username, page, e)
                break
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
                    total += 1

            page += 1

            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        time.sleep(1)

    return total
