"""
Himalayas source. Public browse API, no key required. No verified
keyword-search parameter, so this pulls the raw feed (paginated) and
filters client-side against your query terms, same pattern as RemoteOK.

NOTE: exact JSON field names (title/companyName/etc.) are inferred from
Himalayas' documentation pages, not a directly inspected live response --
worth a quick manual check of the first run's output against real fields
and adjusting the .get() keys below if anything comes back empty.

Attribution requirement from Himalayas' terms: link back to their job URL
and credit Himalayas as source -- apply_url below always uses their url
field directly.
"""

import requests

from .common import JobPosting

BASE_URL = "https://himalayas.app/jobs/api"


def fetch(title_queries, tool_queries, max_pages=10, page_size=100):
    all_terms = [q.lower() for q in title_queries + tool_queries]
    postings = []

    for page in range(max_pages):
        try:
            resp = requests.get(
                BASE_URL, params={"limit": page_size, "offset": page * page_size}, timeout=30
            )
        except requests.exceptions.RequestException as e:
            print(f"[himalayas] page {page} network error, stopping: {e}")
            break

        if resp.status_code != 200:
            print(f"[himalayas] page {page} failed: {resp.status_code}")
            break

        data = resp.json()
        jobs = data.get("jobs", data if isinstance(data, list) else [])
        if not jobs:
            break
        if page == 0:
            print(f"[himalayas] first page returned {len(jobs)} postings, sample keys: {list(jobs[0].keys()) if jobs else 'none'}")

        for job in jobs:
            haystack = " ".join(
                [
                    job.get("title", ""),
                    job.get("excerpt", ""),
                    job.get("description", ""),
                    job.get("companyName", ""),
                ]
            ).lower()

            if not any(term in haystack for term in all_terms):
                continue

            postings.append(
                JobPosting(
                    source="himalayas",
                    external_id=str(job.get("guid") or job.get("id", "")),
                    title=job.get("title", ""),
                    company=job.get("companyName", "Unknown"),
                    location=job.get("locationRestrictions", "Remote") or "Remote",
                    remote=True,
                    description=job.get("description", ""),
                    apply_url=job.get("applicationLink", job.get("url", "")),
                    posted_at=job.get("pubDate"),
                    date_source_reliability="aggregator",
                )
            )

    return postings
