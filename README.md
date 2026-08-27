# Job Scan Pipeline

Personal automation pipeline — daily scan across job boards for AI/automation-analytics-adjacent roles, with dealbreaker filtering, LLM fit scoring, on-demand resume tailoring, and delivery to Google Sheets + Telegram. Runs fully automated via GitHub Actions, once daily at **10:30 UTC (4:00 PM IST)**.

**Status:** Live and automated. Stages 1, 1.5, 2, and 4 run daily with no manual intervention. Stage 3 (resume tailoring) is on-demand only, triggered manually per posting.

## Pipeline stages

- **Stage 1** (`stage1_pull.py`) — pulls postings from 6 sources: Adzuna, RemoteOK, Jobicy, Himalayas, WeWorkRemotely, and full-board pulls from ~28 Tier 2 companies via Greenhouse/Lever/Ashby. Deduplicates within each source's own results.
- **Stage 1.5** (`stage1_5_tag.py`) — pure rule-based dealbreaker tagging, **no LLM, no API cost**. Flags mandatory-degree/experience requirements, and location (a combined blacklist + India whitelist: any location not explicitly matched to India/APAC defaults to excluded, since a blacklist alone can never enumerate every country/city). Also computes posting-age brackets.
- **Stage 2** (`stage2_score.py`) — LLM-based fit scoring (Gemini) against your resume, batched large to stay within free-tier quota. Postings with a hard dealbreaker flag are skipped entirely (no LLM call spent). Uses a **Supabase-backed persistent score cache** (`score_cache.py`) so postings are only ever scored once, even across GitHub Actions' stateless daily runs.
- **Stage 3** (not yet built as a script) — on-demand resume tailoring for specific postings you choose to apply to. Reorders/reweights existing resume content only — never fabricates skills or metrics.
- **Stage 4a** (`stage4_sheets.py`) — writes every scored posting as one row to a Google Sheet (full tracker/log, not just passing ones). Includes a manual Status column for your own Applied/Skipped/Interviewing tracking.
- **Stage 4b** (`telegram_digest.py`) — sends one daily Telegram message with new postings that passed your fit threshold. Shows top 5 by score if more qualify; the rest stay fully visible in the Sheet but don't re-surface on Telegram.

## Dedup architecture

Two independent Supabase tables handle "don't repeat yourself" at two different layers:
- `job_scores` — has this posting ever been scored? (Stage 2's cross-day memory)
- `delivered_postings` — has this posting already been written to the Sheet / sent via Telegram? (per-channel, so a posting can be in the Sheet without ever having triggered a Telegram alert)

## Automation

`.github/workflows/daily_pipeline.yml` runs the full chain (1 → 1.5 → 2 → 4a → 4b) daily. If any step fails, later pipeline steps are skipped (default GitHub Actions behavior), but two safety steps still run regardless: credential cleanup, and a Telegram alert with a direct link to the failed run's logs.

Typical run time: under 2 minutes once the score cache is warm (only genuinely new postings get LLM calls).

## Setup

Credentials needed locally (`.env`, see `.env.example`) and as GitHub Secrets for automation:
`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `GEMINI_API_KEY`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `GOOGLE_SHEET_ID`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID`, plus `GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT` (the full raw JSON key content as a GitHub Secret — locally this is instead `GOOGLE_SERVICE_ACCOUNT_JSON`, a file *path*, not the content; these are deliberately different names for different things, easy to mix up).

Supabase tables required: `delivered_postings`, `job_scores` — see inline `CREATE TABLE` statements in project history, or ask for them again if needed.

## Repo structure

```
src/
  job_sources/          Individual fetchers: adzuna.py, remoteok.py, jobicy.py,
                         himalayas.py, weworkremotely.py, ats_boards.py, common.py
  stage0_extract_resume.py   One-time resume → search query extraction
  stage1_pull.py             Orchestrates all 6 sources
  stage1_5_tag.py            Rule-based dealbreaker tagging (no API cost)
  stage2_score.py            LLM fit scoring + Supabase score cache
  score_cache.py             Supabase read/write for cross-day score persistence
  stage4_sheets.py           Google Sheets writer
  telegram_digest.py         Telegram daily digest sender
  dedup.py                   Supabase read/write for delivery dedup (Sheet/Telegram)
config/
  resume.txt                 Plain-text resume, read by Stage 0 and Stage 2
  query_categories.json      Approved Stage 1 search query list
  company_boards.json        Tier 2 company → ATS slug mapping
.github/workflows/
  daily_pipeline.yml         The automation itself
output/                      Runtime-generated, gitignored — never committed
```

## Known limitations, stated honestly

- **Adzuna and RemoteOK date fields are aggregator-sourced**, not always the true original posting date — age brackets for these sources should be trusted less than Greenhouse/Lever/Ashby's `direct` dates.
- **RemoteOK and Himalayas rarely surface postings matching this niche** — confirmed across multiple real runs, not a bug, just those platforms' audience skew.
- **The location dealbreaker system is a blacklist + whitelist hybrid**, not a perfect classifier — genuinely obscure or malformed location strings could still slip through either direction, though the current design defaults to "exclude" on anything unrecognized, which is the safer failure mode given the stated priority (never see a foreign job) over the alternative (occasionally miss a valid Indian one).
- **Adzuna postings can duplicate across its 15 search queries** — fixed at the source (`adzuna.py` now dedupes within a single run), but data scored before that fix may still show occasional duplicates until a fresh Stage 1 → 2 run replaces it.
