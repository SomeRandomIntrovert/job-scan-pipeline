"""
Stage 4b -- Telegram digest.

Sends a daily message with new postings that:
(a) passed the existing fit threshold,
(b) have recommendation == "Apply", and
(c) haven't already triggered a Telegram alert.

Shows up to 5 postings, ranked by role tier first and score second.
All eligible postings are marked delivered; those not shown remain
available in Google Sheets.
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
    tier = p.get("role_tier", "none")
    recommendation = html.escape(p.get("recommendation", "Apply"))

    loc_raw = p.get("location", "") or (
        "Remote" if p.get("remote") else ""
    )
    location = html.escape(str(loc_raw))
    age = AGE_LABELS.get(
        p.get("age_bracket", ""),
        p.get("age_bracket", "")
    )
    apply_url = html.escape(p.get("apply_url", ""), quote=True)

    return (
        f"{index}. <b>{title}</b> @ {company}\n"
        f"   Tier {tier} · {recommendation} · Score {score}\n"
        f"   {location} · Posted {age}\n"
        f"   <a href=\"{apply_url}\">Apply</a>"
    )


def build_digest_message(new_matches):
    count = len(new_matches)

    if count == 0:
        return None

    def sort_key(p):
        tier = p.get("role_tier", 99)

        # Treat missing/invalid tier as lowest priority.
        try:
            tier = int(tier)
        except (TypeError, ValueError):
            tier = 99

        score = p.get("fit_score", 0)

        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0

        return tier, -score

    top = sorted(new_matches, key=sort_key)[:MAX_SHOWN]

    lines = [
        f"📋 <b>{count} new Apply match{'es' if count != 1 else ''} today</b>"
    ]

    if count > MAX_SHOWN:
        lines.append(
            f"Showing top {MAX_SHOWN} by tier, then score — see your Sheet for the rest."
        )

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

    eligible = [
        p for p in postings
        if p.get("passed_threshold")
        and p.get("recommendation") == "Apply"
    ]

    print(
        f"{len(eligible)} postings passed the threshold "
        "and have recommendation Apply.",
        flush=True,
    )

    client = dedup.get_client()

    eligible_keys = [
        f"{p['source']}|{p['external_id']}"
        for p in eligible
    ]

    undelivered_keys = dedup.get_undelivered(
        client, eligible_keys, "telegram"
    )

    new_matches = [
        p for p in eligible
        if f"{p['source']}|{p['external_id']}" in undelivered_keys
    ]

    print(
        f"{len(new_matches)} new matches not yet sent to Telegram.",
        flush=True,
    )

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
