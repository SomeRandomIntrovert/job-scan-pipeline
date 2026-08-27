"""
Persistent score cache, backed by Supabase -- NOT the local
output/stage2_scored.json file. This distinction matters specifically
for automated daily runs: GitHub Actions gives every run a completely
fresh, empty virtual machine, so a local file has no memory across days.
Supabase does. The local JSON file still gets written each run (Stage 4
reads it within the SAME day's pipeline), but this table is the actual
source of truth for "have we already scored this posting, ever."
"""

CHUNK = 100  # same reasoning as dedup.py -- keep GET-based .in_() filters short


def fetch_cached_scores(client, keys):
    """Returns {key: {fit_score, fit_reasoning, boost_applied, passed_threshold}}
    for whichever of the given keys already have a real score on record."""
    if not keys:
        return {}

    cached = {}
    for i in range(0, len(keys), CHUNK):
        chunk = keys[i : i + CHUNK]
        resp = client.table("job_scores").select("*").in_("key", chunk).execute()
        for row in resp.data:
            cached[row["key"]] = {
                "fit_score": row["fit_score"],
                "fit_reasoning": row["fit_reasoning"],
                "boost_applied": row.get("boost_applied", False),
                "passed_threshold": row.get("passed_threshold", False),
            }
    return cached


def upsert_scores(client, results_map):
    """Persist the full current results_map (carried-forward + dealbreaker
    skips + freshly scored) to Supabase so tomorrow's run can resume from
    here, regardless of which machine actually runs it."""
    if not results_map:
        return

    items = list(results_map.items())
    for i in range(0, len(items), CHUNK):
        chunk = items[i : i + CHUNK]
        rows = [
            {
                "key": key,
                "fit_score": val["fit_score"],
                "fit_reasoning": val["fit_reasoning"],
                "boost_applied": val.get("boost_applied", False),
                "passed_threshold": val.get("passed_threshold", False),
            }
            for key, val in chunk
        ]
        client.table("job_scores").upsert(rows, on_conflict="key").execute()
