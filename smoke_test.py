"""Quick smoke test — verifies all 6 env vars are present and .env loads correctly."""
from dotenv import load_dotenv
import os
import sys

load_dotenv()

REQUIRED = [
    "GETXAPI_KEY",
    "ANTHROPIC_API_KEY",
    "RESEND_API_KEY",
    "EMAIL_TO",
    "X_LIST_ID",
    "X_USERNAME",
]

missing = [v for v in REQUIRED if not os.getenv(v)]

if missing:
    print(f"FAIL — missing env vars: {', '.join(missing)}")
    sys.exit(1)

print("OK — all env vars loaded:")
for v in REQUIRED:
    val = os.getenv(v)
    masked = val[:4] + "..." if len(val) > 4 else "***"
    print(f"  {v} = {masked}")
