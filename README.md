# Fintwit Weekly Analyzer

For agent-assisted operation, start with [AGENTS.md](AGENTS.md). Claude Code loads the same guide through [CLAUDE.md](CLAUDE.md).

Runs every Sunday at 23:30. Pulls $TICKER-bearing tweets from a curated fintwit account list, scores each mention with Claude (sentiment + conviction + rationale), computes weekly metrics, and produces a static HTML dashboard + email summary for Monday-morning reading.

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

## Output

- `dashboard/index.html` — static HTML dashboard, open in any browser
- `reports/YYYY-WW.md` — markdown summary
- Weekly email sent via Resend
