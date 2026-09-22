# Operating FinX Intelligence Agent

Read README.md, run_weekly.py, railway.json and .env.example. The main workflow uses live paid APIs, Postgres writes, gist publication and email. Documentation/testing requests do not authorize a live run.

## Agent workflow

1. Establish the explicit Monday week, roster, database, output destination and authorization for collection/model calls/publication/email. Default windows follow the most recent Friday's trading week and can include an uncompleted Sunday.
2. Configure Python 3.11 and requirements.txt. Privately configure GETXAPI_KEY, X_LIST_ID, ANTHROPIC_API_KEY, DATABASE_URL; delivery uses RESEND_API_KEY, EMAIL_TO, optional RESEND_FROM_EMAIL and GITHUB_TOKEN/GITHUB_USER. X_USERNAME supports following tooling. Report missing names, never values.
3. Inspect config/config.yaml and target database. Importing src.store migrates Postgres: do not import it for an offline check. Management/preflight commands can mutate data or incur API cost.
4. Run only authorized stages. --skip-fetch still calls models, writes the database and may deliver. --no-email disables weekly email AND credit-alert email. --no-publish disables gist publication. Both together suppress external delivery but are NOT an offline analysis mode.
5. Inspect retained tweet coverage, scoring, narrative, Markdown and HTML. A saved report bundle freezes delivery inputs; inspect delivery receipts separately. Overall partial/error exits nonzero.
6. Report actual outputs, estimated costs, missing coverage and delivery receipt. API acceptance is not proof that the recipient read/received the email.

## Commands and recovery

```bash
python run_weekly.py --week <Monday-YYYY-MM-DD>
python run_weekly.py --week <Monday-YYYY-MM-DD> --skip-fetch --no-email --no-publish
python run_weekly.py --deliver-only reports/<Monday-YYYY-MM-DD>.bundle.json
python -m unittest discover -s tests -v
```

--deliver-only avoids collection, scoring and database imports. It runs only enabled publication/email stages and skips those with saved success receipts. Add --no-email and/or --no-publish to constrain it. Both disabled permits a no-network delivery-state check of a supplied bundle.

Successful publication is reused when email has not started. In-flight/uncertain sends are deliberately NOT retried: first reconcile with provider delivery logs, then record the verified outcome in the receipt while preserving an audit copy. No blind resend or deletion of history. Recipient and bundle changes are rejected. To deliberately issue revised content, preserve the original and create a new reviewed bundle.

The direct runner refuses a pre-existing weekly bundle before paid work. Scheduler checks successful DB weeks and refuses existing bundles needing recovery. It uses an exclusive local lock, not a distributed lock: deploy a single worker, persist reports/dashboard on durable storage, and inspect stale locks after crashes. Do not start direct and scheduled runs concurrently.

## Scheduling and remaining limits

Railway configuration invokes scheduled_run.py at 00:00 UTC Saturday (0 0 * * 6). This repository setting is not proof of the deployed scheduler's configuration. The most-recent-Friday window intentionally covers a trading week; verify actual fetch cutoff.

No full offline end-to-end analytics fixture or distributed coordinator exists. Isolated tests cover delivery suppression, receipts, changed inputs, locking and scheduler import safety. smoke_test.py checks presence only, not connectivity; preflight can make paid requests. Delivery-only recovery is local-file dependent and does not rewrite historical weekly_runs success records; the scheduler will keep surfacing incomplete weeks for explicit reconciliation. Keep tweets, handles, reports, caches, contacts and secrets out of public commits.
