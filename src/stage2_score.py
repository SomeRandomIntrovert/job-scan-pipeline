"""
Stage 2 -- JD-to-resume fit scoring. The one stage that genuinely needs an
LLM: judging whether a role's actual responsibilities match your
background and target direction isn't pattern-matching, it's semantic
judgment.

Deliberately scores fit ONLY -- education, experience, location, and work
auth are excluded from this score, since Stage 1.5 already surfaces those
as separate visible flags. Mixing them in here would double-penalize the
same issue under two different mechanisms.

Batched large (see BATCH_SIZE) to keep total API calls far under the
20-requests/day free-tier ceiling discovered the hard way in Stage 1.5.
"""

import json
import os
import time

from google import genai
from google.genai import types
from dotenv import load_dotenv

import dedup
import score_cache

load_dotenv()

INPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "stage1_5_tagged.json")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "stage2_scored.json")
RESUME_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "resume.txt")

BATCH_SIZE = 120  # 14 total calls for 1612 postings -- widened from 110 to leave real retry
                   # margin against your remaining daily quota, not just the bare minimum
MAX_DESC_CHARS = 2500  # JD substance for fit judgment rarely needs more than this
SCORE_THRESHOLD = 65  # postings scoring below this won't reach Stage 4 (Sheet/Telegram) --
                       # ADJUST THIS if 65 turns out too strict or too loose once you see real output
BOOST_POINTS = 5  # applied programmatically for the psych/arts Stage 1.5 boost flag, not re-decided by the LLM

SCORING_PROMPT = """You are scoring job postings for one candidate. Use ONLY the resume text and rules below.

CANDIDATE:
- Approximately 2.5 years of professional experience.
- Background in Trust & Safety, fraud/spam detection, content policy compliance, risk operations, market research, and AI-enabled operations.
- Psychology education and behavioral research background.

TARGET ROLE TIERS:

Tier 1 — Best fit:
- Trust & Safety Analyst / Associate / Specialist
- Scaled Abuse Analyst, Enforcement Detection Analyst, Platform Integrity Analyst
- Fraud Risk, Fraud Operations, Fraud Prevention Analyst
- Risk & Fraud Data Analyst, Risk and Compliance Specialist
- AI Risk Analyst, AI Governance Analyst, Responsible AI Analyst
- AI Policy Compliance Analyst, Content Policy Analyst
- Compliance Associate / Analyst in content review or global compliance
- Policy Enforcement Specialist

Tier 2 — Strong secondary fit:
- AI Operations Analyst
- AI Solution Analyst focused on AI workflows
- AI Knowledge Analyst
- AI and Automation Analyst
- Associate D&T Analyst focused on AI and automation
- Business Process Analyst, Process Automation Analyst
- Operations Analyst, Product Operations Specialist
- Business Intelligence Analyst
- Junior Business Analyst
- Insights and Enablement Analyst

Tier 3 — Opportunistic:
- Governance and Controls QA, especially financial crime
- Junior forensic or fraud investigations roles
- Content moderation Quality and Training Specialist
- Research or market insights analyst

Assign role_tier based on the actual responsibilities, not merely the job title.
Use "none" if the role does not meaningfully fit any tier.

LEVEL:
- Strongly prefer roles asking for 0–3 years of experience.
- Penalize roles asking for 3–4 years.
- Roles requiring 4+ years should generally receive a low score and "Skip".
- Do not mistake preferred qualifications for mandatory requirements.
- Do not score based on age.

SCORING (0–100):
- 40 points: role family and tier match
- 30 points: overlap of required skills with the resume; semantic matches count
- 15 points: experience-level fit
- 10 points: location and work-mode fit
- 5 points: company and environment fit

Subtract points for mandatory tools or skills that the resume does not support.
Do NOT award credit for skills, tools, certifications, or experience absent from the resume.
Do not let a superficial mention of AI make an otherwise unrelated job a strong match.

LOCATION:
Delhi NCR, Hyderabad/Secunderabad, and India-remote roles are preferred.
Other Indian locations are less preferred.
Do not assume remote work is India-based unless the posting supports that.

HONESTY RULES:
- Python and FastAPI: personal-project depth only.
- Snowflake: queried and reported from only.
- No RPA experience.
- No deep ML engineering experience.
- No JavaScript or TypeScript experience.
- Do not exaggerate the candidate's experience or proficiency.
- Use only skills and experience supported by the resume.

RECOMMENDATION:
- "Apply": score >= 70 AND role_tier is 1 or 2.
- "Gate-check": score 50–69 OR role_tier is 3.
- "Skip": score < 50 OR role_tier is "none".
- These recommendations are based on role fit. Stage 1.5 hard-dealbreaker flags are handled separately by the pipeline.

For each posting, return:
- key: exact posting key provided in the input
- score: integer from 0 to 100
- role_tier: 1, 2, 3, or "none"
- recommendation: "Apply", "Gate-check", or "Skip"
- top_matched_keywords: list of at most 3 relevant resume skills or experiences
- red_flags: short list of meaningful mismatches or concerns; use [] if none
- reason: one concise sentence explaining the score

Return ONLY valid JSON. No markdown fences, preamble, or commentary.
Return one object for every posting, using this exact structure:

[
  {{
    "key": "exact-posting-key",
    "score": 78,
    "role_tier": 1,
    "recommendation": "Apply",
    "top_matched_keywords": ["Fraud detection", "Content policy compliance", "SQL"],
    "red_flags": [],
    "reason": "Strong overlap with fraud operations, policy enforcement, and risk analysis."
  }}
]

POSTINGS:
{postings_block}
"""


def build_batch_prompt(resume_text, batch):
    blocks = []
    for p in batch:
        key = f"{p['source']}|{p['external_id']}"
        desc = (p.get("description") or "")[:MAX_DESC_CHARS]
        blocks.append(
            f"KEY: {key}\n"
            f"Title: {p['title']}\n"
            f"Company: {p['company']}\n"
            f"Description: {desc}\n"
        )
    return SCORING_PROMPT.format(resume_text=resume_text, postings_block="\n---\n".join(blocks))


def score_batch(client, resume_text, batch):
    prompt = build_batch_prompt(resume_text, batch)

    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
        )
    except Exception as e:
        print(f"[stage2] Gemini call failed for batch, leaving scores empty: {e}")
        return {}

    raw = (response.text or "").strip()

    # Remove optional Markdown fences.
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[stage2] Could not parse batch response, leaving scores empty: {e}")
        return {}

    if not isinstance(parsed, list):
        print("[stage2] Expected a JSON list; leaving scores empty.")
        return {}

    # Only accept keys belonging to the current batch.
    expected_keys = {
        f"{p['source']}|{p['external_id']}"
        for p in batch
    }

    results = {}

    valid_tiers = {1, 2, 3, "none"}
    valid_recommendations = {"Apply", "Gate-check", "Skip"}

    for item in parsed:
        if not isinstance(item, dict):
            print("[stage2] Ignoring malformed response item.")
            continue

        key = item.get("key")

        if not isinstance(key, str) or key not in expected_keys:
            print(f"[stage2] Ignoring unknown or missing posting key: {key!r}")
            continue

        score = item.get("score")

        # Fail safely if score is missing or invalid.
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            print(f"[stage2] Missing/invalid score for {key}; skipping item.")
            continue

        if not 0 <= score <= 100:
            print(f"[stage2] Out-of-range score for {key}; skipping item.")
            continue

        role_tier = item.get("role_tier", "none")
        if role_tier not in valid_tiers:
            role_tier = "none"

        recommendation = item.get("recommendation", "Skip")
        if recommendation not in valid_recommendations:
            recommendation = "Skip"

        keywords = item.get("top_matched_keywords", [])
        if not isinstance(keywords, list):
            keywords = []
        keywords = [str(x)[:100] for x in keywords if x is not None][:3]

        red_flags = item.get("red_flags", [])
        if not isinstance(red_flags, list):
            red_flags = []
        red_flags = [str(x)[:200] for x in red_flags if x is not None]

        reason = item.get("reason", "")
        if not isinstance(reason, str):
            reason = ""

        results[key] = {
            "key": key,
            "score": int(score),
            "role_tier": role_tier,
            "recommendation": recommendation,
            "top_matched_keywords": keywords,
            "red_flags": red_flags,
            "reason": reason[:500],
        }

    missing_keys = expected_keys - results.keys()
    if missing_keys:
        print(
            f"[stage2] Response omitted {len(missing_keys)} posting(s); "
            "they will remain unscored and can be retried."
        )

    return results


def _apply_and_write(postings, results_map):
    """Write current state to disk. `results_map` must be the FULL current
    knowledge -- carried-forward scores, dealbreaker skips, AND freshly
    scored results, all merged together by the caller BEFORE this is
    called. This function must never be handed a partial view, or
    postings outside that partial view get silently reset to null on
    save -- which is exactly the bug that wiped 459 real scores earlier.
    """
    # Defensive backup: if a previous output file exists, copy it before
    # overwriting, so any future bug in this function can't cause
    # unrecoverable data loss the way today's did.
    if os.path.exists(OUTPUT_PATH):
        backup_path = OUTPUT_PATH + ".bak"
        with open(OUTPUT_PATH, "r", encoding="utf-8") as src, open(backup_path, "w", encoding="utf-8") as dst:
            dst.write(src.read())

    for p in postings:
        key = f"{p['source']}|{p['external_id']}"
        result = results_map.get(key)

        if result is None:
            p["fit_score"] = None
            p["fit_reasoning"] = "Not yet scored"
            p["role_tier"] = "none"
            p["recommendation"] = "Skip"
            p["top_matched_keywords"] = []
            p["red_flags"] = []
            p["boost_applied"] = False
            p["passed_threshold"] = False
        else:
            p["fit_score"] = result["fit_score"]
            p["fit_reasoning"] = result["fit_reasoning"]
            p["role_tier"] = result.get("role_tier", "none")
            p["recommendation"] = result.get("recommendation", "Skip")
            p["top_matched_keywords"] = result.get("top_matched_keywords", [])[:3]
            p["red_flags"] = result.get("red_flags", [])
            p["boost_applied"] = result.get("boost_applied", False)
            p["passed_threshold"] = result["passed_threshold"]

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(postings, f, indent=2)


HARD_DEALBREAKER_PREFIX = "Requires:"  # only this flag type is a true dealbreaker per your
                                        # own design -- "On-site:" is informational, not exclusionary


def has_hard_dealbreaker(posting):
    return any(f.startswith(HARD_DEALBREAKER_PREFIX) for f in posting.get("flags", []))


def main():
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        postings = json.load(f)

    with open(RESUME_PATH, "r", encoding="utf-8") as f:
        resume_text = f.read()

    print(f"Loaded {len(postings)} postings from Stage 1.5.", flush=True)

    postings_by_key = {f"{p['source']}|{p['external_id']}": p for p in postings}

    # Single source of truth for everything we know, from every origin:
    # carried-forward real scores, dealbreaker skips, and (later) freshly
    # scored results. Every save uses this SAME map in full -- never a
    # partial view -- so no category can get silently wiped by another.
    results_map = {}

    # Supabase is the SOURCE OF TRUTH for "already scored" -- not the
    # local file. GitHub Actions gives every automated run a fresh, empty
    # machine, so a local-file-only check would silently forget
    # everything and re-score the whole backlog daily, burning quota for
    # nothing. The local file still gets written below for Stage 4 to
    # read within the SAME day's pipeline, but resume logic reads from
    # here.
    supabase_client = dedup.get_client()
    all_keys = [f"{p['source']}|{p['external_id']}" for p in postings]
    cached_scores = score_cache.fetch_cached_scores(supabase_client, all_keys)
    results_map.update(cached_scores)
    print(f"Found {len(results_map)} already-scored postings in the persistent cache -- will skip these.", flush=True)

    # Re-validate carried-forward postings against CURRENT flags. A
    # posting scored before a Stage 1.5 rule change (e.g. today's
    # EMEA/LATAM/foreign-region fixes) may now carry a hard dealbreaker
    # flag that didn't exist when it was originally scored. The real
    # fit_score/reasoning stay as historical record (no wasted LLM
    # call to redo them), but passed_threshold gets corrected so a
    # since-disqualified posting can't silently keep showing as a
    # valid match in Stage 4.
    revalidated_count = 0
    for p in postings:
        key = f"{p['source']}|{p['external_id']}"
        if key in results_map and results_map[key]["passed_threshold"] and has_hard_dealbreaker(p):
            results_map[key]["passed_threshold"] = False
            results_map[key]["fit_reasoning"] += " [Re-flagged: now has a hard dealbreaker per updated Stage 1.5 rules -- original score kept for reference, but this no longer passes]"
            revalidated_count += 1
    if revalidated_count:
        print(f"{revalidated_count} previously-scored posting(s) re-flagged as disqualified due to updated Stage 1.5 rules (original scores preserved, passed_threshold corrected).", flush=True)

    to_score = []
    skipped_dealbreaker_count = 0
    for p in postings:
        key = f"{p['source']}|{p['external_id']}"
        if key in results_map:
            continue  # already have a real prior score, nothing to do
        elif has_hard_dealbreaker(p):
            results_map[key] = {
                "fit_score": None,
                "fit_reasoning": "Not scored -- hard dealbreaker already disqualifies (see flags)",
                "boost_applied": False,
                "passed_threshold": False,
            }
            skipped_dealbreaker_count += 1
        else:
            to_score.append(p)

    print(f"{skipped_dealbreaker_count} postings skipped (hard dealbreaker -- not worth an API call).", flush=True)
    print(f"{len(to_score)} postings still need scoring.", flush=True)

    # Save immediately after sorting carry-forward/dealbreaker/to-score,
    # even before any new API calls -- so this state is safely on disk
    # regardless of what happens next. Also persist to Supabase at the
    # same point, so even a dealbreaker-only run (nothing to actually
    # score) still saves its determinations for tomorrow.
    _apply_and_write(postings, results_map)
    score_cache.upsert_scores(supabase_client, results_map)

    if not to_score:
        print("Nothing left to score -- all postings already accounted for.", flush=True)
        return

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY not set. Fill in .env before running Stage 2.")

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=120_000))

    num_batches = (len(to_score) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(to_score), BATCH_SIZE):
        batch = to_score[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"Scoring batch {batch_num}/{num_batches} ({len(batch)} postings)...", flush=True)
        batch_results = score_batch(client, resume_text, batch)

        for key, r in batch_results.items():
            p = postings_by_key.get(key)
            if p is None:
                continue

            raw_score = r.get("score", 0)
            boost_applied = (
                "Boost: psych/arts/social-science preferred"
                in p.get("flags", [])
            )
            final_score = (
                min(100, raw_score + BOOST_POINTS)
                if boost_applied
                else raw_score
            )

            results_map[key] = {
                "fit_score": final_score,
                "fit_reasoning": r.get("reason", ""),
                "role_tier": r.get("role_tier", "none"),
                "recommendation": r.get("recommendation", "Skip"),
                "top_matched_keywords": r.get("top_matched_keywords", [])[:3],
                "red_flags": r.get("red_flags", []),
                "boost_applied": boost_applied,
                "passed_threshold": final_score >= SCORE_THRESHOLD,
            }

        # Incremental save after EVERY batch, using the FULL results_map
        # (carried-forward + dealbreaker-skips + everything scored so far
        # this run) -- never just this batch's results alone. Same
        # incremental persistence to Supabase, so a crash mid-run doesn't
        # lose today's real, expensive LLM-scored results for tomorrow.
        _apply_and_write(postings, results_map)
        score_cache.upsert_scores(supabase_client, results_map)
        newly_scored_so_far = sum(1 for v in results_map.values() if v["fit_score"] is not None)
        print(f"  -> progress saved ({newly_scored_so_far} total postings with a real score so far)", flush=True)

        if i + BATCH_SIZE < len(to_score):
            time.sleep(2)  # courtesy pacing between the (few) large calls

    scored_count = sum(1 for p in postings if p.get("fit_score") is not None)
    passed_count = sum(1 for p in postings if p.get("passed_threshold"))
    print(f"\n{scored_count} of {len(postings)} postings successfully scored.", flush=True)
    print(f"{passed_count} postings scored >= {SCORE_THRESHOLD} (will reach Stage 4).", flush=True)
    print(f"Written to {OUTPUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
