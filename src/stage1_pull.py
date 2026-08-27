"""
Stage 1 orchestrator. Runs every source, normalizes everything to
JobPosting, and dumps the raw combined result to output/stage1_raw.json
for manual review -- nothing is filtered, scored, or deduped yet. That's
Stage 1.5 / Stage 2 / Supabase dedupe, built separately.

This script intentionally does the LEAST amount of decision-making
possible -- its only job is "did we successfully pull data from every
source." Review the output file before building anything downstream.
"""

import json
import os

from dotenv import load_dotenv

from job_sources import adzuna, remoteok, jobicy, himalayas, weworkremotely, ats_boards

load_dotenv()

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "stage1_raw.json")
QUERY_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "query_categories.json")


def load_queries():
    with open(QUERY_CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    return config["stage1_title_queries"], config["stage1_tool_keyword_queries"]


def main():
    title_queries, tool_queries = load_queries()

    all_postings = []
    source_counts = {}

    print("Fetching Adzuna...")
    result = adzuna.fetch(title_queries, tool_queries)
    all_postings.extend(result)
    source_counts["adzuna"] = len(result)

    print("Fetching RemoteOK...")
    result = remoteok.fetch(title_queries, tool_queries)
    all_postings.extend(result)
    source_counts["remoteok"] = len(result)

    print("Fetching Jobicy...")
    result = jobicy.fetch(title_queries, tool_queries)
    all_postings.extend(result)
    source_counts["jobicy"] = len(result)

    print("Fetching Himalayas...")
    result = himalayas.fetch(title_queries, tool_queries)
    all_postings.extend(result)
    source_counts["himalayas"] = len(result)

    print("Fetching WeWorkRemotely...")
    result = weworkremotely.fetch(title_queries, tool_queries)
    all_postings.extend(result)
    source_counts["weworkremotely"] = len(result)

    print("Fetching Tier 2 company boards...")
    result = ats_boards.fetch()
    all_postings.extend(result)
    source_counts["ats_boards"] = len(result)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump([p.to_dict() for p in all_postings], f, indent=2)

    print("\n--- Stage 1 summary ---")
    for source, count in source_counts.items():
        print(f"{source}: {count} raw postings")
    print(f"Total: {len(all_postings)} raw postings (before any filtering/dedup)")
    print(f"Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
