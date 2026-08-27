"""
Shared dedup layer for Stage 4. Both the Sheets writer and the Telegram
sender use this so a posting is never added to the Sheet twice or
alerted on Telegram more than once, no matter how many times the daily
pipeline runs.
"""

import os

from supabase import create_client
from dotenv import load_dotenv

load_dotenv()


def get_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SECRET_KEY not set. Fill in .env before running Stage 4.")
    return create_client(url, key)


def get_undelivered(client, keys, channel):
    """Given a list of posting keys, return the subset NOT yet marked
    delivered for the given channel ('sheet' or 'telegram'). Postings
    with no row at all in delivered_postings are treated as undelivered."""
    column = f"delivered_to_{channel}"
    if not keys:
        return set()

    # Supabase/Postgrest has a practical limit on IN-clause size; chunk
    # defensively rather than assume the whole backlog fits in one call.
    # NOTE: .in_() builds a comma-separated list directly in the URL query
    # string (this is a GET request), not the request body -- a chunk of
    # 500 keys like "greenhouse:company|1234567" can produce a 15,000+
    # character URL that gets rejected by the gateway with a blunt,
    # non-JSON "Bad Request" before ever reaching the database. Keep this
    # small.
    delivered_keys = set()
    CHUNK = 100
    for i in range(0, len(keys), CHUNK):
        chunk = keys[i : i + CHUNK]
        resp = client.table("delivered_postings").select("key").in_("key", chunk).eq(column, True).execute()
        delivered_keys.update(row["key"] for row in resp.data)

    return set(keys) - delivered_keys


def mark_delivered(client, keys, channel):
    """Batch upsert delivery status. Postgres upsert (INSERT ... ON
    CONFLICT DO UPDATE) only touches the columns actually specified here
    -- it does NOT reset the other channel's flag or any other column to
    its default, so this is safe to call independently for 'sheet' and
    'telegram' without clobbering each other.

    De-dupes the incoming key list first: a single ON CONFLICT DO UPDATE
    statement can't touch the same row twice, so if the SAME posting
    appears more than once in the source data (a real bug traced back to
    Adzuna's multi-query fetch not deduping across its own 15 queries),
    Postgres rejects the whole batch outright rather than silently
    picking one. De-duping here makes this call robust regardless of
    whether the upstream data is clean yet."""
    if not keys:
        return

    keys = list(dict.fromkeys(keys))  # de-dupe while preserving order

    column = f"delivered_to_{channel}"
    CHUNK = 200
    for i in range(0, len(keys), CHUNK):
        chunk = keys[i : i + CHUNK]
        rows = [{"key": k, column: True} for k in chunk]
        client.table("delivered_postings").upsert(rows, on_conflict="key").execute()
