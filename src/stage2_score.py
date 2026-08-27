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

SCORING_PROMPT = """You are scoring job postings for ROLE FIT against a candidate's background.
Score ONLY on whether the role's actual responsibilities and required
skills match what this candidate does and is moving toward. Do NOT factor
in education requirements, years-of-experience requirements, location, or
work authorization -- those are handled separately and must not affect
this score.

The candidate is NOT narrowly targeting "AI governance" roles specifically
-- they are targeting the broader AI + Automation Analytics space:
governance, enablement, operations, strategy, knowledge management, and
business process improvement all count as strong fits. Do not
under-score a role just because it's operations-flavored rather than
governance-flavored -- both are equally the target, per the candidate's
own stated direction.

IMPORTANT: the candidate's resume includes product management frameworks
and methodologies (Agile, Scrum, RICE, MoSCoW, A/B testing, etc.) and a
completed PM certification. This is SUPPORTING evidence that satisfies a
common requirement in many AI/automation/analytics postings -- it is NOT
a signal that the candidate is targeting Product Manager roles as a
category. A posting titled "Product Manager" with no AI/automation/
analytics component should NOT score well just because the candidate has
PM literacy. Conversely, do not penalize a strong AI/automation/analytics
role for also listing PM methodologies as a requirement -- the candidate
already meets that requirement, so it should not count against the score
either way.

CANDIDATE BACKGROUND:
---
{resume_text}
---

For each posting, output a fit score from 0-100 and a one-sentence
reasoning (max 25 words) grounded in actual responsibility/skill overlap,
not generic praise.

Return ONLY valid JSON, no markdown fences, no preamble, in this exact
shape:

[
  {{"key": "...", "score": 78, "reasoning": "..."}},
  {{"key": "...", "score": 22, "reasoning": "..."}}
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
        response = client.models.generate_content(model="gemini-3.6-flash", contents=prompt)
    except Exception as e:
        print(f"[stage2] Gemini call failed for batch, leaving scores empty: {e}")
        return {}

    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[stage2] Could not parse batch response, leaving scores empty: {e}")
        return {}

    return {item["key"]: item for item in parsed}


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
            p["boost_applied"] = False
            p["passed_threshold"] = False
        else:
            p["fit_score"] = result["fit_score"]
            p["fit_reasoning"] = result["fit_reasoning"]
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
            boost_applied = "Boost: psych/arts/social-science preferred" in p.get("flags", [])
            final_score = min(100, raw_score + BOOST_POINTS) if boost_applied else raw_score
            results_map[key] = {
                "fit_score": final_score,
                "fit_reasoning": r.get("reasoning", ""),
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
