"""
RemoteOK source. Public JSON feed, no API key required. All postings here
are remote by definition. Same aggregator caveat on posted date as Adzuna
-- RemoteOK's `date` field is generally reliable but not guaranteed to be
the original posting date on every listing, so still tagged "aggregator"
to be safe rather than overclaiming.
"""

import requests

from .common import JobPosting

FEED_URL = "https://remoteok.com/api"


def fetch(title_queries, tool_queries):
    try:
        resp = requests.get(FEED_URL, headers={"User-Agent": "job-scan-pipeline/1.0"}, timeout=30)
    except requests.exceptions.RequestException as e:
        print(f"[remoteok] network error, skipping source: {e}")
        return []

    if resp.status_code != 200:
        print(f"[remoteok] fetch failed: {resp.status_code}")
        return []

    data = resp.json()
    # RemoteOK's feed returns a legend object as item [0] -- skip it
    raw_postings = [item for item in data if isinstance(item, dict) and item.get("id")]
    print(f"[remoteok] fetched {len(raw_postings)} total postings before keyword filtering")

    all_terms = [q.lower() for q in title_queries + tool_queries]
    postings = []

    for item in raw_postings:
        haystack = " ".join(
            [item.get("position", ""), item.get("description", ""), " ".join(item.get("tags", []))]
        ).lower()

        if not any(term in haystack for term in all_terms):
            continue

        postings.append(
            JobPosting(
                source="remoteok",
                external_id=str(item.get("id")),
                title=item.get("position", ""),
                company=item.get("company", "Unknown"),
                location=item.get("location", "Remote"),
                remote=True,
                description=item.get("description", ""),
                apply_url=item.get("url", ""),
                posted_at=item.get("date"),
                date_source_reliability="aggregator",
            )
        )

    return postings
