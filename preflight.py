"""
Slice 2 pre-flight: verifies GetXAPI cashtag search works and response shape
matches what fetch.py will expect. All four checks must pass before Slice 3.

Caches the API response to data/preflight_cache.json — re-runs use the cache
by default to avoid burning API credits. Pass --fresh to force a new API call.
"""
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

CACHE_PATH = Path("data/preflight_cache.json")
FRESH = "--fresh" in sys.argv

GETXAPI_KEY = os.getenv("GETXAPI_KEY")
X_USERNAME = os.getenv("X_USERNAME")

if not GETXAPI_KEY or not X_USERNAME:
    print("ABORT — GETXAPI_KEY and X_USERNAME must be set in .env before running preflight.")
    sys.exit(1)

if not FRESH and CACHE_PATH.exists():
    print(f"Using cached response ({CACHE_PATH}). Pass --fresh to re-fetch.\n")
    data = json.loads(CACHE_PATH.read_text())
else:
    today = date.today()
    since = (today - timedelta(days=7)).isoformat()
    until = today.isoformat()
    query = f"$ from:{X_USERNAME} since:{since} until:{until}"
    print(f"Query: {query}\n")

    resp = requests.get(
        "https://api.getxapi.com/twitter/tweet/advanced_search",
        headers={"Authorization": f"Bearer {GETXAPI_KEY}"},
        params={"q": query, "product": "Latest"},
        timeout=15,
    )

    print(f"Status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"PRE-FLIGHT FAILED — HTTP {resp.status_code}")
        sys.exit(1)

    data = resp.json()
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, indent=2))
    print(f"Response cached to {CACHE_PATH}\n")

print("Raw response:")
print(json.dumps(data, indent=2))
print()

tweets = data.get("tweets") or data.get("data") or []

# Exact cashtag: $ immediately followed by 1–5 uppercase letters, bounded on
# both sides — won't fire on mid-word $ or run-on letters like $AAPLS
cashtag_re = re.compile(r'(?<![A-Z\$])\$[A-Z]{1,5}(?![A-Z])')

results = {}

results["At least one tweet returned"] = len(tweets) > 0

results["At least one tweet contains an exact $TICKER cashtag"] = any(
    cashtag_re.search(t.get("text") or "") for t in tweets
) if tweets else False

results["has_more field present"] = "has_more" in data
results["next_cursor field present"] = "next_cursor" in data

results["Each tweet has author.userName"] = all(
    (t.get("author") or {}).get("userName") for t in tweets
) if tweets else False

print("=" * 50)
all_passed = True
for check, passed in results.items():
    status = "PASS" if passed else "FAIL"
    if not passed:
        all_passed = False
    print(f"  [{status}] {check}")

print("=" * 50)
if all_passed:
    print("PRE-FLIGHT PASSED — safe to proceed to Slice 3")
else:
    print("PRE-FLIGHT FAILED — do not proceed")
    sys.exit(1)
