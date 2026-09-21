#!/usr/bin/env python3
"""
manage.py — Fintwit Weekly maintenance CLI.

Commands:
  list-accounts             List all active accounts with source + tweet count
  sync-roster               Manual roster refresh
  rescore --week YYYY-MM-DD Re-run Claude scoring for a past week
  rerun   --week YYYY-MM-DD Full re-run of a past week
  set-cap @handle N         Set tweet_cap_override for an account
  show-caps                 List all cap overrides
  stats                     API usage, DB size, recent runs
  add-to-list @h1 @h2 ...   Add accounts to X list and DB roster
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).resolve().parent


def _migrate_extras():
    """Add columns that were not in the original schema."""
    from src import store
    with store.get_db() as conn:
        try:
            conn.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS tweet_cap_override INTEGER")
        except Exception:
            pass  # already exists


def _fmt_num(n):
    return f"{n:,}" if n is not None else "—"


# ── list-accounts ──────────────────────────────────────────────────────────────

def cmd_list_accounts(args):
    _migrate_extras()
    from src import store
    with store.get_db() as conn:
        rows = conn.execute("""
            SELECT a.username, a.source, a.followers, a.tweet_cap_override,
                   COUNT(DISTINCT t.id) AS tweet_count
            FROM accounts a
            LEFT JOIN tweets t ON t.user_id = a.user_id
            WHERE a.active = 1
            GROUP BY a.user_id
            ORDER BY tweet_count DESC
        """).fetchall()

    print(f"\n{'Handle':<25} {'Source':<12} {'Followers':>10} {'Cap':>5} {'Tweets':>7}")
    print("─" * 65)
    for r in rows:
        cap = str(r["tweet_cap_override"]) if r["tweet_cap_override"] is not None else "—"
        print(f"@{r['username']:<24} {(r['source'] or '—'):<12} "
              f"{_fmt_num(r['followers']):>10} {cap:>5} {_fmt_num(r['tweet_count']):>7}")
    print(f"\n{len(rows)} active accounts")


# ── sync-roster ────────────────────────────────────────────────────────────────

def cmd_sync_roster(args):
    _migrate_extras()
    from src import fetch
    added, removed = fetch.sync_roster()
    print(f"Roster sync complete: +{added} added, -{removed} removed")


# ── rescore ────────────────────────────────────────────────────────────────────

def cmd_rescore(args):
    _migrate_extras()
    if not args.week:
        print("Error: --week YYYY-MM-DD required")
        sys.exit(1)

    ws = args.week
    from datetime import date, timedelta
    week_start = date.fromisoformat(ws)
    week_end   = (week_start + timedelta(days=6)).isoformat()
    week_start = week_start.isoformat()

    # Clear existing scores for the week
    from src import store
    with store.get_db() as conn:
        n = conn.execute("""
            UPDATE mentions SET sentiment=NULL, conviction=NULL, rationale=NULL, scored_at=NULL
            WHERE week_start = ?
        """, (week_start,)).rowcount
    print(f"Cleared {n} existing scores for week {week_start}")

    from src.analyze_claude import score_mentions
    count, cost = score_mentions(week_start, week_end)
    print(f"Scored: {count} mentions  |  estimated cost: ${cost:.4f}")


# ── rerun ──────────────────────────────────────────────────────────────────────

def cmd_rerun(args):
    _migrate_extras()
    if not args.week:
        print("Error: --week YYYY-MM-DD required")
        sys.exit(1)

    ws = args.week
    from datetime import date, timedelta
    week_start = date.fromisoformat(ws)
    week_end   = (week_start + timedelta(days=6)).isoformat()
    week_start = week_start.isoformat()

    print(f"Re-running full pipeline for week {week_start} → {week_end}")

    from src import fetch, extract, store
    from src.analyze_claude import score_mentions, divergence_summaries, weekly_narrative
    from src.analyze_compute import top_mentioned, wow_movers, new_tickers
    from src.report_html import generate_dashboard

    print("Fetching tweets...")
    fetch.fetch_tweets(week_start, week_end)

    print("Extracting mentions...")
    extract.process_new_tweets(week_start)

    print("Scoring mentions...")
    count, cost = score_mentions(week_start, week_end)
    print(f"  Scored {count}  |  cost ${cost:.4f}")

    print("Generating divergence summaries...")
    summaries = divergence_summaries(week_start, week_end)
    print(f"  {len(summaries)} summaries")

    metrics = {
        "top": top_mentioned(week_start, week_end),
        "wow": wow_movers(week_start, week_end),
        "new": new_tickers(week_start, week_end),
    }
    print("Generating narrative...")
    metrics["narrative"] = weekly_narrative(week_start, week_end, metrics)

    print("Regenerating dashboard...")
    path = generate_dashboard(week_start, week_end)
    print(f"  {path}")
    print("Done.")


# ── set-cap ────────────────────────────────────────────────────────────────────

def cmd_set_cap(args):
    _migrate_extras()
    if not args.handle or args.cap is None:
        print("Usage: manage.py set-cap @handle N")
        sys.exit(1)

    handle = args.handle.lstrip("@")
    cap    = int(args.cap)

    from src import store
    with store.get_db() as conn:
        n = conn.execute(
            "UPDATE accounts SET tweet_cap_override=? WHERE username=?",
            (cap, handle)
        ).rowcount

    if n:
        print(f"Set cap for @{handle} → {cap} pages")
    else:
        print(f"Account @{handle} not found")


# ── show-caps ──────────────────────────────────────────────────────────────────

def cmd_show_caps(args):
    _migrate_extras()
    from src import store
    with store.get_db() as conn:
        rows = conn.execute("""
            SELECT username, tweet_cap_override
            FROM accounts
            WHERE tweet_cap_override IS NOT NULL
            ORDER BY username
        """).fetchall()

    if not rows:
        print("No cap overrides set.")
        return

    print(f"\n{'Handle':<25} {'Cap':>5}")
    print("─" * 32)
    for r in rows:
        print(f"@{r['username']:<24} {r['tweet_cap_override']:>5}")
    print(f"\n{len(rows)} override(s)")


# ── stats ──────────────────────────────────────────────────────────────────────

def cmd_stats(args):
    _migrate_extras()
    from src import store

    db_path = _ROOT / "data" / "fintwit.db"
    db_size = db_path.stat().st_size / 1024 / 1024 if db_path.exists() else 0

    with store.get_db() as conn:
        accounts  = conn.execute("SELECT COUNT(*) FROM accounts WHERE active=1").fetchone()[0]
        tweets    = conn.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
        mentions  = conn.execute("SELECT COUNT(*) FROM mentions").fetchone()[0]
        scored    = conn.execute("SELECT COUNT(*) FROM mentions WHERE scored_at IS NOT NULL").fetchone()[0]
        runs      = conn.execute("""
            SELECT week_start, run_at, status, tweets_fetched, mentions_extracted, api_cost_usd
            FROM weekly_runs ORDER BY run_at DESC LIMIT 5
        """).fetchall()

    print(f"\n{'DB size:':<22} {db_size:.1f} MB")
    print(f"{'Active accounts:':<22} {_fmt_num(accounts)}")
    print(f"{'Total tweets:':<22} {_fmt_num(tweets)}")
    print(f"{'Total mentions:':<22} {_fmt_num(mentions)}")
    print(f"{'Scored mentions:':<22} {_fmt_num(scored)} ({scored/mentions*100:.0f}%)" if mentions else "")

    if runs:
        print(f"\n{'Recent runs':}")
        print(f"  {'Week':<12} {'Run at':<22} {'Status':<10} {'Tweets':>7} {'Mentions':>9} {'Cost':>8}")
        print("  " + "─" * 75)
        for r in runs:
            print(f"  {r['week_start']:<12} {r['run_at'][:19]:<22} {r['status']:<10} "
                  f"{_fmt_num(r['tweets_fetched']):>7} {_fmt_num(r['mentions_extracted']):>9} "
                  f"${r['api_cost_usd'] or 0:.4f}")


# ── add-to-list ────────────────────────────────────────────────────────────────

def cmd_add_to_list(args):
    _migrate_extras()
    if not args.handles:
        print("Usage: manage.py add-to-list @handle1 @handle2 ...")
        sys.exit(1)

    handles = [h.lstrip("@") for h in args.handles]

    import requests
    from src import store

    api_key  = os.environ.get("GETX_API_KEY")
    list_id  = os.environ.get("X_LIST_ID", "").rstrip("/").split("/")[-1]

    if not api_key:
        print("Error: GETX_API_KEY not set")
        sys.exit(1)

    added = []
    now   = datetime.now(timezone.utc).isoformat()

    for handle in handles:
        # Resolve user_id via GetXAPI user lookup
        r = requests.get(
            "https://api.getxapi.com/v2/twitter/user/details",
            params={"userName": handle},
            headers={"x-api-key": api_key},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"  @{handle}: lookup failed ({r.status_code})")
            continue

        data    = r.json()
        user    = data.get("data", {})
        user_id = user.get("id") or user.get("user_id")
        if not user_id:
            print(f"  @{handle}: no user_id in response")
            continue

        # Add to X list
        add_r = requests.post(
            f"https://api.getxapi.com/v2/twitter/list/{list_id}/members",
            json={"userId": user_id},
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            timeout=30,
        )
        if add_r.status_code not in (200, 201):
            print(f"  @{handle}: add-to-list failed ({add_r.status_code}) — adding to DB only")

        # Upsert into DB
        with store.get_db() as conn:
            store.upsert_account(
                conn,
                user_id=user_id,
                username=handle,
                display_name=user.get("name"),
                followers=user.get("followersCount"),
                following=user.get("followingCount"),
                is_blue_verified=int(bool(user.get("isBlueVerified"))),
                source="manual",
                now=now,
            )
            conn.execute(
                "INSERT INTO roster_changes(username, change_type, reason, changed_at) VALUES (?,?,?,?)",
                (handle, "added", "manual add-to-list", now)
            )

        print(f"  @{handle} ({user_id}): added")
        added.append(handle)

    print(f"\n{len(added)}/{len(handles)} accounts added")


# ── CLI parser ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="manage.py",
        description="Fintwit Weekly maintenance CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-accounts", help="List all active accounts")

    sub.add_parser("sync-roster", help="Manual roster refresh")

    p_rescore = sub.add_parser("rescore", help="Re-score a past week")
    p_rescore.add_argument("--week", metavar="YYYY-MM-DD", help="Week start (Monday)")

    p_rerun = sub.add_parser("rerun", help="Full re-run of a past week")
    p_rerun.add_argument("--week", metavar="YYYY-MM-DD", help="Week start (Monday)")

    p_cap = sub.add_parser("set-cap", help="Set tweet_cap_override for an account")
    p_cap.add_argument("handle", help="@handle")
    p_cap.add_argument("cap", type=int, help="Max pages (e.g. 10)")

    sub.add_parser("show-caps", help="List all cap overrides")

    sub.add_parser("stats", help="DB size, API usage, recent runs")

    p_add = sub.add_parser("add-to-list", help="Add accounts to X list + DB roster")
    p_add.add_argument("handles", nargs="+", help="@handle1 @handle2 ...")

    args = parser.parse_args()

    dispatch = {
        "list-accounts": cmd_list_accounts,
        "sync-roster":   cmd_sync_roster,
        "rescore":       cmd_rescore,
        "rerun":         cmd_rerun,
        "set-cap":       cmd_set_cap,
        "show-caps":     cmd_show_caps,
        "stats":         cmd_stats,
        "add-to-list":   cmd_add_to_list,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
