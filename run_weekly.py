#!/usr/bin/env python3
"""
run_weekly.py — Main weekly pipeline orchestrator.

Default run: computes week window automatically (last Monday → last Sunday).
  python3 run_weekly.py

Re-run a specific week (pass the Monday):
  python3 run_weekly.py --week 2026-04-27
"""

import argparse
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

_ROOT = Path(__file__).resolve().parent


def classify_credit_error(exc):
    """
    Return 'claude', 'getxapi', or None.

    Precise on purpose — we only want to fire a "top up your credits" alert for
    real upstream rejections, never for transient timeouts or unrelated bugs:
      • 'claude'  — Anthropic 400 whose message names the credit balance.
      • 'getxapi' — a GetXAPI HTTP 4xx (auth/payment/rate). Timeouts (ReadTimeout)
                    and 5xx are NOT classified, so they keep the existing
                    warn-and-continue / generic-error behaviour.
    """
    import anthropic
    import requests

    if isinstance(exc, anthropic.BadRequestError) and "credit balance" in str(exc).lower():
        return "claude"

    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        if exc.response.status_code in (401, 402, 403, 429):
            return "getxapi"

    return None


def _rerun_command(week_start, skip_fetch=False):
    """The exact copy-paste terminal command to resume a stalled week."""
    cmd = f"cd {_ROOT} && python3 run_weekly.py --week {week_start}"
    if skip_fetch:
        cmd += " --skip-fetch"
    return cmd


def _week_window(week_arg=None):
    """
    Returns (week_start, week_end) as ISO date strings.
    week_start is always a Monday, week_end the following Sunday.
    If week_arg is given (a Monday date string), use that directly.
    Otherwise derive the most recent completed Mon-Sun window.
    """
    if week_arg:
        ws = date.fromisoformat(week_arg)
        we = ws + timedelta(days=6)
        return ws.isoformat(), we.isoformat()

    today = date.today()
    # Anchor on the trading week that just closed: the week containing the most
    # recent Friday (Friday counts as itself). Tuned for the Saturday-01:00 cron
    # so it processes the week whose Friday close just happened, rather than
    # lagging a week (which the old "last completed Sun" logic would do on a Sat).
    days_since_friday = (today.weekday() - 4) % 7
    last_friday = today - timedelta(days=days_since_friday)
    week_start = last_friday - timedelta(days=4)   # that week's Monday
    week_end   = last_friday + timedelta(days=2)   # that week's Sunday
    return week_start.isoformat(), week_end.isoformat()


def _step(n, label):
    print(f"\n[{n}] {label}")


def main():
    parser = argparse.ArgumentParser(description="Fintwit Weekly pipeline")
    parser.add_argument("--week", metavar="YYYY-MM-DD",
                        help="Week start (Monday) to run; defaults to last completed week")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="Skip roster sync and tweet fetch (use when tweets already in DB)")
    parser.add_argument("--no-email", action="store_true",
                        help="Skip sending the weekly email")
    args = parser.parse_args()

    week_start, week_end = _week_window(args.week)
    run_at = datetime.now(timezone.utc).isoformat()

    print(f"=== Fintwit Weekly pipeline ===")
    print(f"    Week: {week_start} → {week_end}")
    print(f"    Run at: {run_at[:19]} UTC")

    from src import store, fetch, extract, analyze_compute
    from src.analyze_claude import score_mentions, divergence_summaries, weekly_narrative, new_ticker_summaries
    from src import report_md, report_html, gist as gist_mod
    from src import email as email_mod

    tweets_fetched      = 0
    mentions_extracted  = 0
    claude_cost         = 0.0
    new_following       = []
    narrative           = ""
    status              = "success"
    notes               = []

    try:
        # ── 0. Sync roster ────────────────────────────────────────────────────
        if args.skip_fetch:
            _step(0, "Sync roster — SKIPPED (--skip-fetch)")
        else:
            _step(0, "Sync roster")
            try:
                added, unchanged = fetch.sync_roster()
                print(f"    +{added} added, {unchanged} unchanged")
            except Exception as e:
                # Credit/auth rejections must not be swallowed — let them reach
                # the main handler so an alert email goes out. Transient errors
                # (timeouts, 5xx) still warn and continue on existing accounts.
                if classify_credit_error(e):
                    raise
                print(f"    [WARN] Roster sync failed: {e} — continuing with existing accounts")

        # ── 1. Week window already computed above ─────────────────────────────
        _step(1, f"Week window: {week_start} → {week_end}")

        # ── 2. Fetch tweets ───────────────────────────────────────────────────
        if args.skip_fetch:
            _step(2, "Fetch tweets — SKIPPED (--skip-fetch)")
        else:
            _step(2, "Fetch tweets")
            tweets_fetched = fetch.fetch_tweets(week_start, week_end)
            print(f"    {tweets_fetched:,} tweets fetched")

        # ── 3. Extract tickers ────────────────────────────────────────────────
        _step(3, "Extract tickers")
        mentions_extracted = extract.process_new_tweets()
        print(f"    {mentions_extracted:,} mentions extracted")

        # ── 3b. Tag theme tweets ──────────────────────────────────────────────
        with store.get_db() as conn:
            conn.execute("ALTER TABLE tweets ADD COLUMN IF NOT EXISTS has_theme_word INTEGER DEFAULT 0")
            result = conn.execute(
                "UPDATE tweets SET has_theme_word = 1"
                " WHERE has_theme_word = 0 AND lower(text) LIKE '%theme%'"
            )
            tagged = result.rowcount
        print(f"    {tagged:,} theme tweets tagged")

        # ── 4. Score mentions ─────────────────────────────────────────────────
        _step(4, "Score mentions (Claude Haiku)")
        scored, claude_cost = score_mentions(week_start, week_end)
        print(f"    {scored:,} scored  |  est. cost ${claude_cost:.4f}")

        # ── 5. Compute metrics ────────────────────────────────────────────────
        _step(5, "Compute metrics")
        top  = analyze_compute.top_mentioned(week_start, week_end)
        uniq = analyze_compute.unique_mentioners(week_start, week_end)
        wow  = analyze_compute.wow_movers(week_start, week_end)
        mom  = analyze_compute.mom_movers(week_start, week_end)
        new  = analyze_compute.new_tickers(week_start, week_end)
        print(f"    top={len(top)} tickers  wow={len(wow)}  new={len(new)}")

        metrics = {"top": top, "uniq": uniq, "wow": wow, "mom": mom, "new": new}

        # ── 6. New ticker summaries ───────────────────────────────────────────
        _step(6, "New ticker summaries (Claude Sonnet)")
        new_ticker_data = new_ticker_summaries(week_start, week_end)
        metrics["new_ticker_summaries"] = new_ticker_data
        print(f"    {len(new_ticker_data)} new tickers classified")

        # ── 7. Divergence summaries ───────────────────────────────────────────
        _step(7, "Divergence summaries (Claude Sonnet)")
        summaries = divergence_summaries(week_start, week_end)
        print(f"    {len(summaries)} ticker summaries written")

        # ── 8. Weekly narrative ───────────────────────────────────────────────
        _step(8, "Weekly narrative (Claude Sonnet)")
        narrative = weekly_narrative(week_start, week_end, metrics)
        metrics["narrative"] = narrative
        print(f"    {len(narrative)} chars generated")

        # ── 9. Markdown report ────────────────────────────────────────────────
        _step(9, "Generate markdown report")
        md_path = report_md.generate_report(week_start, week_end)
        print(f"    {md_path}")

        # ── 10. HTML dashboard ────────────────────────────────────────────────
        _step(10, "Generate HTML dashboard")
        html_path = report_html.generate_dashboard(week_start, week_end, new_ticker_data=new_ticker_data)
        print(f"    {html_path}")

        # ── 11. Publish gist ──────────────────────────────────────────────────
        _step(11, "Publish dashboard gist")
        dashboard_url = None
        try:
            dashboard_url = gist_mod.publish_dashboard(week_start, html_path)
            print(f"    {dashboard_url}")
        except Exception as e:
            print(f"    [WARN] Gist publish failed: {e}")

        _step(12, "Send weekly email")
        if args.no_email:
            print("    SKIPPED (--no-email)")
        else:
            try:
                result = email_mod.send_weekly_email(
                    week_start, week_end, metrics,
                    new_following=new_following,
                    narrative=narrative,
                    new_ticker_summaries=new_ticker_data,
                    dashboard_url=dashboard_url,
                )
                print(f"    Sent — id: {getattr(result, 'id', result)}")
            except Exception as e:
                print(f"    [WARN] Email send failed: {e}")
                notes.append(f"email_failed: {e}")

    except Exception as e:
        status = "error"
        tb = traceback.format_exc()
        notes.append(tb.strip().splitlines()[-1])
        print(f"\n[ERROR] Pipeline failed:\n{tb}", file=sys.stderr)

        # If the failure was a credit/auth rejection from GetXAPI or Claude,
        # still send an email — with the exact command to resume after topping up.
        kind = classify_credit_error(e)
        if kind:
            # Claude steps all run after the fetch, so tweets are already in the
            # DB — resume with --skip-fetch to avoid re-paying GetXAPI. A GetXAPI
            # failure means we need a full re-run.
            command = _rerun_command(week_start, skip_fetch=(kind == "claude"))
            if kind == "claude":
                reason = "Anthropic credit balance too low to access the API."
            else:
                code = getattr(getattr(e, "response", None), "status_code", "?")
                reason = (f"GetXAPI rejected the request (HTTP {code}) — "
                          f"likely credits exhausted (or an API-key / rate-limit issue).")
            print(f"\n[CREDIT STALL] {kind}: sending alert email with resume command...")
            try:
                email_mod.send_alert_email(kind, week_start, week_end, command, reason)
                print("    Alert email sent.")
                notes.append(f"{kind}_credit_stall_alerted")
            except Exception as alert_err:
                print(f"    [WARN] Alert email failed: {alert_err}", file=sys.stderr)
                notes.append(f"alert_email_failed: {alert_err}")

    # ── 12. Log run ───────────────────────────────────────────────────────────
    _step(12, "Log run to DB")
    with store.get_db() as conn:
        accounts_active = conn.execute(
            "SELECT COUNT(*) FROM accounts WHERE active=1"
        ).fetchone()[0]
        store.log_run(
            conn,
            run_at=run_at,
            week_start=week_start,
            accounts_fetched=accounts_active,
            tweets_fetched=tweets_fetched,
            mentions_extracted=mentions_extracted,
            api_cost_usd=claude_cost,
            status=status,
            notes="; ".join(notes) if notes else None,
        )
    print(f"    status={status}")

    print(f"\n=== Done. Week {week_start} → {week_end}  |  status={status} ===\n")

    if status == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
