"""
Persistent score cache, backed by Supabase.

CR-06:
- Versioned cache keys invalidate scores from the previous rubric.
- New scoring metadata is stored inside fit_reasoning.
- No Supabase schema changes are required.
"""

import json

CHUNK = 100

# Increment this whenever scoring rubric or scoring interpretation changes.
CACHE_VERSION = "cr06-v1"

META_MARKER = "\n\n[CR06_META_JSON]"


def make_cache_key(key):
    """Return a versioned Supabase key for a posting."""
    return f"{CACHE_VERSION}|{key}"


def _encode_reasoning(reasoning, metadata):
    """Store readable reasoning and metadata in the existing column."""
    payload = {
        "role_tier": metadata.get("role_tier", "none"),
        "recommendation": metadata.get("recommendation", "Skip"),
        "top_matched_keywords": metadata.get("top_matched_keywords", [])[:3],
        "red_flags": metadata.get("red_flags", []),
    }

    return (
        f"{reasoning or ''}"
        f"{META_MARKER}{json.dumps(payload, ensure_ascii=False)}"
    )


def _decode_reasoning(stored_reasoning):
    """Extract readable reasoning and CR-06 metadata from cache."""
    stored_reasoning = stored_reasoning or ""

    if META_MARKER not in stored_reasoning:
        return stored_reasoning, {
            "role_tier": "none",
            "recommendation": "Skip",
            "top_matched_keywords": [],
            "red_flags": [],
        }

    reasoning, metadata_json = stored_reasoning.split(META_MARKER, 1)

    try:
        metadata = json.loads(metadata_json)
    except json.JSONDecodeError:
        metadata = {}

    return reasoning, {
        "role_tier": metadata.get("role_tier", "none"),
        "recommendation": metadata.get("recommendation", "Skip"),
        "top_matched_keywords": metadata.get("top_matched_keywords", [])[:3],
        "red_flags": metadata.get("red_flags", []),
    }


def fetch_cached_scores(client, keys):
    """
    Returns cached results keyed by ORIGINAL posting key.

    Only scores stored under the current rubric version are retrieved.
    """
    if not keys:
        return {}

    versioned_keys = [make_cache_key(key) for key in keys]
    cached = {}

    for i in range(0, len(versioned_keys), CHUNK):
        chunk = versioned_keys[i:i + CHUNK]

        resp = (
            client.table("job_scores")
            .select("*")
            .in_("key", chunk)
            .execute()
        )

        for row in resp.data:
            versioned_key = row["key"]

            if not versioned_key.startswith(f"{CACHE_VERSION}|"):
                continue

            original_key = versioned_key[len(CACHE_VERSION) + 1:]
            reasoning, metadata = _decode_reasoning(
                row.get("fit_reasoning", "")
            )

            cached[original_key] = {
                "fit_score": row["fit_score"],
                "fit_reasoning": reasoning,
                "boost_applied": row.get("boost_applied", False),
                "passed_threshold": row.get("passed_threshold", False),
                **metadata,
            }

    return cached


def upsert_scores(client, results_map):
    """
    Persist the full results_map using versioned cache keys.
    Original posting keys remain unchanged in the pipeline.
    """
    if not results_map:
        return

    items = list(results_map.items())

    for i in range(0, len(items), CHUNK):
        chunk = items[i:i + CHUNK]

        rows = []

        for key, val in chunk:
            metadata = {
                "role_tier": val.get("role_tier", "none"),
                "recommendation": val.get("recommendation", "Skip"),
                "top_matched_keywords": val.get("top_matched_keywords", [])[:3],
                "red_flags": val.get("red_flags", []),
            }

            rows.append({
                "key": make_cache_key(key),
                "fit_score": val["fit_score"],
                "fit_reasoning": _encode_reasoning(
                    val.get("fit_reasoning", ""),
                    metadata,
                ),
                "boost_applied": val.get("boost_applied", False),
                "passed_threshold": val.get("passed_threshold", False),
            })

        client.table("job_scores").upsert(
            rows,
            on_conflict="key",
        ).execute()
