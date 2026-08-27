"""
Stage 0 — Categorized resume extraction.

Reads config/resume.txt and asks Gemini to split your resume signal into
three buckets, then drafts a Stage 1 search query list weighted toward
where you're actually heading — not just what your current title says.

Run this once locally, review the output, edit query_categories.json by
hand if needed, then commit it. Stage 1 reads the committed file — it
does NOT re-run this automatically, so your approved list stays stable
until you deliberately re-run this script.
"""

import json
import os

import google.genai as genai
from dotenv import load_dotenv

load_dotenv()

RESUME_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "resume.txt")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "query_categories.json")

EXTRACTION_PROMPT = """You are analyzing a resume to help build a job search tool. Split the
resume's signal into three categories:

A) CORE OPERATIONAL SKILLS — day-to-day tools/skills from their current role
   (dashboards, reporting, SQL, process management, etc.)

B) AI/AUTOMATION BUILD SKILLS — technical skills for building AI/automation
   systems (Python, APIs, LLM orchestration, workflow tools, etc.)

C) TARGET-ROLE SIGNALS — forward-facing signal specifically from certifications,
   personal/portfolio projects, and any explicitly aspirational skill listed
   (NOT from their current job description — only from what points toward
   where they're headed, not where they've been)

Then draft 6-10 Stage 1 job search queries. Rules for the queries:
- Weight toward B and C — these are underrepresented in raw resume text but
  are the actual target direction
- Use A only as context, never as a primary query term
- Cover a BROAD field: AI/automation analytics, enablement, operations,
  strategy, governance, knowledge management, business process improvement.
  Do NOT narrow to just one of these sub-areas.
- Each query should be a realistic job-search phrase (2-4 words), not a
  skill name

Resume text:
---
{resume_text}
---

Return ONLY valid JSON, no markdown fences, no preamble, in this exact shape:
{{
  "category_a_core_operational": ["...", "..."],
  "category_b_ai_automation_build": ["...", "..."],
  "category_c_target_role_signals": ["...", "..."],
  "stage1_queries": ["...", "..."]
}}
"""


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY not set. Copy .env.example to .env and fill it in.")

    if not os.path.exists(RESUME_PATH):
        raise SystemExit(
            f"Missing {RESUME_PATH}. Paste your resume's text content into config/resume.txt first."
        )

    with open(RESUME_PATH, "r", encoding="utf-8") as f:
        resume_text = f.read()

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model="gemini-3.6-flash", contents=EXTRACTION_PROMPT.format(resume_text=resume_text)
    )
    raw = response.text.strip()

    # Defensive cleanup in case the model wraps output in markdown fences anyway
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    parsed = json.loads(raw)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2)

    print(f"Wrote {OUTPUT_PATH}")
    print(json.dumps(parsed, indent=2))
    print(
        "\nReview this output. Edit config/query_categories.json by hand if anything looks off, "
        "then commit it before running Stage 1."
    )


if __name__ == "__main__":
    main()
