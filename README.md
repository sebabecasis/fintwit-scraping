# Fintwit Weekly Analyzer

For agent-assisted operation, start with [AGENTS.md](AGENTS.md). Claude Code loads the same guide through [CLAUDE.md](CLAUDE.md).

Railway is configured for Saturday 00:00 UTC through the single-host scheduled_run.py guard. Pulls $TICKER-bearing tweets from a curated fintwit account list, scores each mention with Claude (sentiment + conviction + rationale), computes weekly metrics, and produces a static HTML dashboard + email summary for Monday-morning reading.

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure env vars
cp .env.example .env
# Edit .env with your keys

# 3. Run
python run_weekly.py
```

## Env vars

| Var | Description |
|---|---|
| `GETXAPI_KEY` | GetXAPI key for tweet fetching |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude scoring |
| `RESEND_API_KEY` | Resend key for email delivery |
| `EMAIL_TO` | Recipient email address |
| `X_LIST_ID` | Numeric X List ID to pull accounts from |
| `X_USERNAME` | Your X username (without @) |
| `DATABASE_URL` | Supabase Postgres connection string |
| `GITHUB_TOKEN`, `GITHUB_USER` | Dashboard gist publication |
| `RESEND_FROM_EMAIL` | Optional verified sender |

## Output

- `dashboard/index.html` — static HTML dashboard, open in any browser
- `reports/YYYY-WW.md` — markdown summary
- Weekly email sent via Resend

## Controlled delivery and recovery

`--no-email` suppresses all email, including credit alerts. `--no-publish` suppresses gist publication. These flags do not disable paid collection, model calls or database writes.

Completed reports freeze inputs and HTML in `reports/<Monday>.bundle.json`. `python run_weekly.py --deliver-only <bundle>` resumes only delivery, without fetching, scoring or importing the database. Successful stages are skipped using persisted receipts. Uncertain/in-flight outcomes require provider reconciliation before retrying, to avoid duplicate email. Delivery failures now result in nonzero partial status.

Persist reports on durable storage. `scheduled_run.py` is single-host guarded and import-safe, not a distributed scheduler. The direct runner refuses an existing report bundle before paid work. Do not run direct and scheduled workers concurrently. See [AGENTS.md](AGENTS.md) for recovery and schedule limitations.

Offline contract tests: `python -m unittest discover -s tests -v`. They do not execute live analytics or paid providers.
