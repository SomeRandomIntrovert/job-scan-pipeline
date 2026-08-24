# Job Scan Pipeline

Personal automation pipeline — daily scan across job boards for AI/automation-analytics-adjacent roles, with dealbreaker filtering, LLM fit scoring, on-demand resume tailoring, and a Google Sheets tracker.

**Status:** Build in progress. Stage 0 (resume extraction) is the current step.

## Pipeline stages

- **Stage 0** — one-time categorized resume extraction → produces the search query list (needs manual approval before Stage 1 runs)
- **Stage 1** — broad pull across Tier 1 (Adzuna, RemoteOK, Jobicy, Himalayas, WeWorkRemotely) + Tier 2 (Greenhouse/Lever/Ashby company boards)
- **Stage 1.5** — dealbreaker tagging: education, experience, location/remote, work authorization (visible tags, never a silent drop)
- **Stage 1.6** — posting-age bracket + date-source reliability tag
- **Stage 2** — LLM fit scoring against JD substance (not job title)
- **Stage 3** — on-demand resume tailoring (reorder/reweight existing content only, no fabrication)
- **Stage 4** — write to Google Sheet + daily Telegram digest (10:30 AM IST)
- **Stage 5** — periodic scoring recalibration from your Applied/Skipped decisions

## Setup

Credentials needed (see `.env.example`): Adzuna API key, Telegram bot token + user ID, Google service account JSON, Supabase URL + secret key, Gemini API key. All go into GitHub Actions Secrets — never committed.
