"""
Stage 4b -- Telegram digest. Sends a single daily message with new
postings that (a) passed your fit threshold and (b) haven't already
triggered a Telegram alert before. If more than 5 qualify, shows the
top 5 by score; the rest are still marked delivered (Option A) -- they
stay fully visible in the Sheet, they just don't re-surface here.
"""

import html
import json
import os

import requests
from dotenv import load_dotenv

import dedup

load_dotenv()

INPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "stage2_scored.json")
MAX_SHOWN = 5

AGE_LABELS = {
    "0-24h": "in the last 24h",
    "24-72h": "24-72h ago",
    "72h-1wk": "3-7 days ago",
    ">1wk": "over a week ago",
    "unknown": "posting date unknown",
}


def format_posting(p, index):
    title = html.escape(p.get("title", "Untitled"))
    company = html.escape(p.get("company", "Unknown"))
    score = p.get("fit_score", "")
    loc_raw = p.get("location", "") or ("Remote" if p.get("remote") else "")
    location = html.escape(loc_raw)
    age = AGE_LABELS.get(p.get("age_bracket", ""), p.get("age_bracket", ""))
    apply_url = p.get("apply_url", "")

    return (
        f"{index}. <b>{title}</b> @ {company} — {score}\n"
        f"   {location} · Posted {age}\n"
        f"   <a href=\"{apply_url}\">Apply</a>"
    )


def build_digest_message(new_matches):
    count = len(new_matches)

    if count == 0:
        return None  # caller sends the "no new matches" message directly

    top = sorted(new_matches, key=lambda p: p.get("fit_score", 0), reverse=True)[:MAX_SHOWN]

    lines = [f"📋 <b>{count} new match{'es' if count != 1 else ''} today</b>"]
    if count > MAX_SHOWN:
        lines.append(f"Showing top {MAX_SHOWN} by score — see your Sheet for the rest.")
    lines.append("")

    for i, p in enumerate(top, start=1):
        lines.append(format_posting(p, i))
        lines.append("")

    return "\n".join(lines).strip()


def send_telegram_message(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_USER_ID")
    if not token or not chat_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN / TELEGRAM_USER_ID not set. Fill in .env before running Stage 4b.")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise SystemExit(f"Telegram send failed: {resp.status_code} {resp.text[:300]}")
    return resp.json()


def main():
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        postings = json.load(f)

    print(f"Loaded {len(postings)} postings from Stage 2.", flush=True)

    passed = [p for p in postings if p.get("passed_threshold")]
    print(f"{len(passed)} postings passed the fit threshold.", flush=True)

    client = dedup.get_client()
    passed_keys = [f"{p['source']}|{p['external_id']}" for p in passed]
    undelivered_keys = dedup.get_undelivered(client, passed_keys, "telegram")

    new_matches = [p for p in passed if f"{p['source']}|{p['external_id']}" in undelivered_keys]
    print(f"{len(new_matches)} new matches not yet sent to Telegram.", flush=True)

    if not new_matches:
        send_telegram_message("📋 No new matches today (score above threshold).")
        print("Sent 'no new matches' message.", flush=True)
        return

    message = build_digest_message(new_matches)
    send_telegram_message(message)
    print(f"Sent digest with {min(len(new_matches), MAX_SHOWN)} shown of {len(new_matches)} new matches.", flush=True)

    all_new_keys = [f"{p['source']}|{p['external_id']}" for p in new_matches]
    dedup.mark_delivered(client, all_new_keys, "telegram")
    print(f"Marked {len(all_new_keys)} postings as delivered to Telegram.", flush=True)


if __name__ == "__main__":
    main()
