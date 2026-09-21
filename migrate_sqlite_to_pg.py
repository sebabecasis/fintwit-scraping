#!/usr/bin/env python3
"""
migrate_sqlite_to_pg.py — one-off copy of data/fintwit.db (SQLite) into the
Supabase Postgres database pointed to by DATABASE_URL.

Run ONCE, after the Supabase project exists and DATABASE_URL is set:

    pip install -r requirements.txt
    python3 migrate_sqlite_to_pg.py

It truncates the target tables first (safe on a fresh DB), copies every row
preserving primary keys, resets identity sequences, and prints a per-table
sqlite-vs-postgres row-count check at the end. Idempotent — re-running reloads
from scratch.
"""

import os
import sqlite3
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv()

# Importing store ensures the Postgres schema exists (store.migrate() runs on
# import) before we load data into it.
from src import store  # noqa: E402  (import after load_dotenv on purpose)

SQLITE_PATH = "data/fintwit.db"

# Insertion order respects foreign keys: accounts <- tweets <- mentions.
TABLES = ["accounts", "tweets", "mentions", "weekly_runs", "roster_changes", "themes"]
# Tables whose integer PK is an IDENTITY column; sequence reset after load.
IDENTITY_TABLES = ["mentions", "weekly_runs", "roster_changes", "themes"]


def main():
    if not os.path.exists(SQLITE_PATH):
        sys.exit(f"SQLite DB not found at {SQLITE_PATH}")
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL not set")

    src = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row

    with psycopg.connect(dsn, autocommit=False, prepare_threshold=None) as pg:
        with pg.cursor() as cur:
            cur.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")

            for t in TABLES:
                rows = src.execute(f"SELECT * FROM {t}").fetchall()
                if not rows:
                    print(f"  {t}: 0 rows (skipped)")
                    continue
                cols = rows[0].keys()
                collist = ", ".join(cols)
                placeholders = ", ".join(["%s"] * len(cols))
                cur.executemany(
                    f"INSERT INTO {t} ({collist}) VALUES ({placeholders})",
                    [tuple(r[c] for c in cols) for r in rows],
                )
                print(f"  {t}: inserted {len(rows):,}")

            # Advance each IDENTITY sequence past the max copied id so future
            # inserts don't collide with preserved primary keys.
            for t in IDENTITY_TABLES:
                cur.execute(
                    f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {t}), 1))"
                )
        pg.commit()

    # Verify: compare row counts table-by-table.
    print("\n== row-count check (sqlite vs postgres) ==")
    ok = True
    with psycopg.connect(dsn, prepare_threshold=None) as pg:
        with pg.cursor() as cur:
            for t in TABLES:
                s = src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                p = cur.fetchone()[0]
                flag = "OK" if s == p else "MISMATCH"
                ok = ok and s == p
                print(f"  {t:14} sqlite={s:>7,}  pg={p:>7,}  {flag}")
    print("\nALL GOOD ✅" if ok else "\n!! MISMATCH — investigate before deleting the SQLite file")


if __name__ == "__main__":
    main()
