#!/usr/bin/env python3
"""
scheduled_run.py — idempotent entry point for the launchd schedule.

Invoked by the LaunchAgent on BOTH triggers:
  • the weekly time trigger (Sunday 23:30), and
  • run-at-load (every login / boot) as a catch-up guard.

It computes the most recently completed Mon–Sun window (identical logic to
run_weekly.py), checks weekly_runs for a *successful* run of that week, and
only runs the pipeline if one is missing. This makes the schedule robust to
the Mac being asleep or shut down at 23:30 — the next login fills the gap —
while never double-processing (or double-emailing) a week already done.
"""

import subprocess
import sys
from datetime import datetime, timezone

from run_weekly import _week_window  # reuse the exact week-window logic
from src import store

week_start, week_end = _week_window()
stamp = datetime.now(timezone.utc).isoformat()[:19]

with store.get_db() as conn:
    already_done = conn.execute(
        "SELECT 1 FROM weekly_runs WHERE week_start = ? AND status = 'success' LIMIT 1",
        (week_start,),
    ).fetchone()

if already_done:
    print(f"[{stamp}Z] scheduled_run: week {week_start} → {week_end} "
          f"already completed successfully — skipping.")
    sys.exit(0)

print(f"[{stamp}Z] scheduled_run: week {week_start} → {week_end} "
      f"not yet completed — running pipeline.")
result = subprocess.run([sys.executable, "run_weekly.py"])
sys.exit(result.returncode)
