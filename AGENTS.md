# Operating FinX Intelligence Agent

This repository powers the portfolio's FinX Intelligence Agent. Read `README.md`, `run_weekly.py`, `railway.json` and `.env.example` before running it. Unlike the other portfolio fixtures, its main workflow uses live paid APIs, writes Postgres, publishes a dashboard gist and sends email. No complete offline fixture mode exists.

## Agent workflow

1. Establish the requested week, roster, database, desired outputs and whether collection, model calls, gist publication and email delivery are authorized. Reuse standing authorization where its scope matches. Documentation or audit requests do not authorize a live run.
2. Use Python 3.11 and an isolated environment with `requirements.txt`. Configure secrets privately from `.env.example`: collection uses `GETXAPI_KEY` and `X_LIST_ID`; analysis uses `ANTHROPIC_API_KEY`; storage requires `DATABASE_URL`; delivery uses `RESEND_API_KEY`, `EMAIL_TO` and optionally `RESEND_FROM_EMAIL`; gist publication uses `GITHUB_TOKEN` and `GITHUB_USER`. `X_USERNAME` supports following/preflight tooling. Report missing variable names, never values.
3. Inspect `config/config.yaml` and the target database/schema before operating. The code uses Supabase Postgres, not a local SQLite database. Database helpers and management commands can migrate schema; do not assume a statistics command is read-only.
4. Choose an explicit Monday date for reproducible runs. The default window anchors on the most recent Friday and labels its Monday–Sunday week; it can include a Sunday that has not happened yet. Verify fetched-data coverage separately from the displayed period.
5. After an authorized run, inspect generated Markdown and HTML, compare the reported week with the data, review ticker/sentiment/conviction evidence and inspect delivery outcomes. A success status alone does not prove an email arrived.
6. Report the output paths, collection/scoring coverage, available cost information, warnings and actual publication/delivery outcomes. Treat roster changes and investor interpretations as proposals for review.

## Execution and recovery

Run from the repository root after configuring the authorized environment:

```bash
python run_weekly.py --week <YYYY-MM-DD>
```

To reuse already-fetched tweets after an interrupted analysis:

```bash
python run_weekly.py --week <YYYY-MM-DD> --skip-fetch
```

`--skip-fetch` skips roster sync and fetching only; it still calls models, writes the database, publishes a gist and sends email. `--no-email` skips the normal weekly email only: the error handler can still send a credit/auth alert, and gist publication still runs. Neither flag is a dry run. Do not run the pipeline as a documentation check or assume it is safe merely because these flags are set.

Outputs include `reports/` Markdown, `dashboard/` HTML and `weekly_runs` database entries. Inspect the paths returned by the actual run. Preserve cached tweets on failure, diagnose the failing stage, and avoid repeating successful paid collection. Rerunning can republish and resend: the direct runner is not protected by the wrapper's successful-week check.

## Known operational gaps

- README and `scheduled_run.py` describe Sunday 23:30, while `railway.json` specifies `0 0 * * 6` and starts `run_weekly.py` directly. Confirm the real deployed scheduler before claiming its cadence. The wrapper's check does not protect the configured direct Railway entry point or concurrent runs.
- Gist/email failures can warn while the overall run remains successful. A successful-week check can then prevent retrying failed delivery.
- There is no flag to disable every external publication/delivery path. A true local/report-only mode needs code changes and tests.
- `manage.py add-to-list` reads `GETX_API_KEY`, while normal collection uses `GETXAPI_KEY`. Resolve that mismatch before using it.
- `preflight.py` performs a paid network request when its cache is absent; `smoke_test.py` checks only part of the configuration and prints credential prefixes. Neither is a complete offline test suite.

For documentation checks, parse Python files without importing or executing them. Never import `scheduled_run.py` to inspect it: it has top-level execution. For code changes, add targeted isolated tests around the affected stage, with API/database/email clients replaced by test doubles. Keep real handles, tweets, reports, credentials and database exports out of public commits.
