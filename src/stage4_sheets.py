"""
Stage 4a -- Google Sheets writer. Appends every scored posting that
hasn't already been written to the Sheet, using the dedup table so
reruns never create duplicate rows. This is the full tracker/log --
EVERY scored posting gets a row here (not just ones that passed the fit
threshold), so you have a complete history to sort/filter yourself, plus
a manual status column to track your own progress (Applied/Skipped/
Interviewing).
"""

import json
import os
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

import dedup

load_dotenv()

INPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "stage2_scored.json")

IST = timezone(timedelta(hours=5, minutes=30))

SHEET_HEADERS = [
    "Date Added to Sheet", "Date Posted", "Title", "Company", "Score", "Role Tier", "Recommendation", "Top Matching Keywords", "Red Flags",
    "Passed Threshold", "Age Bracket", "Flags", "Location", "Remote",
    "JD Link", "Fit Reasoning", "Status",
]


def get_sheet():
    creds_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not creds_path or not sheet_id:
        raise SystemExit("GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SHEET_ID not set. Fill in .env before running Stage 4.")

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id).sheet1


def _cell_safe(value):
    """Defensively coerce a value before it goes into a Sheet cell. Some
    source data (observed: at least one RemoteOK posting's location
    field) comes through as a list instead of a plain string -- the
    Sheets API rejects list values outright with a fairly opaque error.
    Numbers are left as-is so the Score column stays numeric, not text."""
    if isinstance(value, list):
        return "; ".join(str(v) for v in value if v)
    if value is None:
        return ""
    return value


def posting_to_row(p, date_added_to_sheet):
    return [
        date_added_to_sheet,
        _cell_safe(p.get("posted_at", "")),
        _cell_safe(p.get("title", "")),
        _cell_safe(p.get("company", "")),
        p.get("fit_score") if p.get("fit_score") is not None else "",
        p.get("role_tier", "none"),
        p.get("recommendation", "Skip"),
        "; ".join(p.get("top_matched_keywords", [])),
        "; ".join(p.get("red_flags", [])),
        "Yes" if p.get("passed_threshold") else "No",
        _cell_safe(p.get("age_bracket", "")),
        "; ".join(p.get("flags", [])),
        _cell_safe(p.get("location", "")),
        "Yes" if p.get("remote") else "No",
        _cell_safe(p.get("apply_url", "")),
        _cell_safe(p.get("fit_reasoning", "")),
        "",  # Status -- left blank for you to fill in
    ]


def main():
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        postings = json.load(f)

    print(f"Loaded {len(postings)} postings from Stage 2.", flush=True)

    client = dedup.get_client()
    all_keys = [f"{p['source']}|{p['external_id']}" for p in postings]
    undelivered_keys = dedup.get_undelivered(client, all_keys, "sheet")

    to_write = [p for p in postings if f"{p['source']}|{p['external_id']}" in undelivered_keys]
    print(f"{len(to_write)} postings not yet in the Sheet -- writing these.", flush=True)

    if not to_write:
        print("Nothing new to write.", flush=True)
        return

    sheet = get_sheet()

    # Check specifically for a real header row, not just "is the sheet
    # empty" -- given how many partial/crashed runs happened before this
    # got fully working, the sheet likely already had some data by the
    # time a run finally succeeded, so an "is empty" check silently never
    # added headers at all. This also self-heals your CURRENT sheet: if
    # headers are missing, insert them at row 1 (pushing existing data
    # down) rather than requiring a rebuild.
    first_row = sheet.row_values(1)
    if first_row != SHEET_HEADERS:
        sheet.insert_row(SHEET_HEADERS, index=1)
        print("Headers were missing -- inserted at row 1.", flush=True)

    # One shared timestamp for the whole run -- every row written today
    # gets the identical value, so filtering the Sheet by this column
    # cleanly groups "everything the pipeline added in this run."
    date_added_to_sheet = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")

    rows = [posting_to_row(p, date_added_to_sheet) for p in to_write]

    CHUNK = 500
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i : i + CHUNK]
        sheet.append_rows(chunk, value_input_option="USER_ENTERED")
        print(f"  -> wrote rows {i+1}-{i+len(chunk)} of {len(rows)}", flush=True)

    written_keys = [f"{p['source']}|{p['external_id']}" for p in to_write]
    dedup.mark_delivered(client, written_keys, "sheet")

    print(f"\nDone. {len(to_write)} new rows written to the Sheet.", flush=True)


if __name__ == "__main__":
    main()
