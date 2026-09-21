from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta
from src import store
from src.analyze_claude import extract_themes, compare_themes, _save_themes


def _available_days():
    with store.get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT substr(created_at, 1, 10) AS day FROM tweets ORDER BY day DESC"
        ).fetchall()
    return [r["day"] for r in rows]


def _available_weeks():
    with store.get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT substr(created_at, 1, 10) AS day FROM tweets ORDER BY day DESC"
        ).fetchall()
    days = [r["day"] for r in rows]
    seen, weeks = set(), []
    for day in days:
        dt = datetime.strptime(day, "%Y-%m-%d")
        monday = dt - timedelta(days=dt.weekday())
        key = monday.strftime("%Y-%m-%d")
        if key not in seen:
            seen.add(key)
            sunday = monday + timedelta(days=6)
            display = f"Week of {key}  ({monday.strftime('%b %d')} – {sunday.strftime('%b %d')})"
            until = (monday + timedelta(days=7)).strftime("%Y-%m-%d")
            weeks.append((display, key, until))
            seen.add(key)
    return weeks


def _pick(options, prompt):
    for i, label in enumerate(options, 1):
        print(f"  {i}. {label}")
    while True:
        raw = input(f"\n{prompt} [1-{len(options)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print("  Invalid choice, try again.")


# ── step 1: pick date range ───────────────────────────────────────────────────
print("\n=== Theme Extractor ===\n")
print("Filter by:")
print("  1. Day")
print("  2. Week")
print("  3. All tweets")

choice = input("\nChoice [1-3]: ").strip()

if choice == "1":
    days = _available_days()
    if not days:
        print("No tweets in DB.")
        exit()
    idx = _pick(days, "Pick a day")
    day = days[idx]
    since = day
    until = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    label = f"themes_{day}"

elif choice == "2":
    weeks = _available_weeks()
    if not weeks:
        print("No tweets in DB.")
        exit()
    idx = _pick([w[0] for w in weeks], "Pick a week")
    _, since, until = weeks[idx]
    label = f"themes_{since}"

else:
    since = until = None
    label = "themes_all"

# ── step 2: extract ───────────────────────────────────────────────────────────
print()
themes = extract_themes(since=since, until=until, label=label)

if not themes:
    exit()

# ── step 3: compare against known themes ─────────────────────────────────────
print("\nComparing against known themes…\n")
matched, novel = compare_themes(themes)

if matched:
    print(f"{len(matched)} themes matched to existing:\n")
    for m in matched:
        print(f"  '{m['new']}' → '{m['existing']}'")
    print()

# ── step 4: review novel themes ───────────────────────────────────────────────
if not novel:
    print("No novel themes — all matched existing.\n")
else:
    print(f"{len(novel)} new themes to review (y to keep, n to discard):\n")
    approved = []
    for t in novel:
        print(f"  {t['theme']}")
        if t.get("narrative"):
            print(f"  {t['narrative']}")
        if t.get("tickers"):
            print(f"  Tickers: {', '.join(t['tickers'])}")
        if t.get("accounts"):
            print(f"  Accounts: {', '.join('@' + a for a in t['accounts'])}")
        ans = input("  Keep? [y/n]: ").strip().lower()
        if ans == "y":
            approved.append(t)
        print()

    if approved:
        _save_themes(f"{label}_approved", approved)
        print(f"{len(approved)} new themes saved under '{label}_approved'.")
    else:
        print("No new themes kept.")
