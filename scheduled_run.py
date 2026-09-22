#!/usr/bin/env python3
"""Single-host scheduler guard. Persist reports; this is not a distributed lock."""
import subprocess
import sys
from pathlib import Path


def main():
    from run_weekly import _week_window
    from src import store
    root = Path(__file__).resolve().parent
    week_start, _ = _week_window()
    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    lock = reports / "scheduled.lock"
    try:
        handle = lock.open("x")
    except FileExistsError:
        print("Run active or interrupted; inspect scheduled.lock before recovery")
        return 1
    with handle:
        try:
            with store.get_db() as conn:
                done = conn.execute("SELECT 1 FROM weekly_runs WHERE week_start = ? AND status = 'success' LIMIT 1", (week_start,)).fetchone()
            if done:
                print(f"Week {week_start} already successful; skipping")
                return 0
            bundle = reports / f"{week_start}.bundle.json"
            if bundle.exists():
                print(f"Existing report: use run_weekly.py --deliver-only {bundle}; inspect delivery receipt first")
                return 1
            return subprocess.run([sys.executable, "run_weekly.py", "--week", week_start], cwd=root).returncode
        finally:
            lock.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
