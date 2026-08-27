"""
Jobicy source. Public API, no key required. Rate limit note from their
own docs: automated checks must not run more than once/hour -- our daily
cron is well within that. A small delay is added between the 15 queries
in a single run as a courtesy, since firing them all instantly resembles
abusive traffic even though it's one logical run.

Attribution requirement from Jobicy's terms: keep their original job URL
when displaying a listing (already the default -- apply_url below always
uses their url field directly, never rehosted).
"""

import time

import requests

from .common import JobPosting

BASE_URL = "https://jobicy.com/api/v2/remote-jobs"


def fetch(title_queries, tool_queries, count=50):
    postings = []
    seen_ids = set()

    for i, query in enumerate(title_queries + tool_queries):
        if i > 0:
            time.sleep(1)  # courtesy delay between requests

        try:
            resp = requests.get(BASE_URL, params={"count": count, "tag": query}, timeout=30)
        except requests.exceptions.RequestException as e:
            print(f"[jobicy] query '{query}' network error, skipping: {e}")
            continue

        if resp.status_code != 200:
            print(f"[jobicy] query '{query}' failed: {resp.status_code}")
            continue

        for job in resp.json().get("jobs", []):
            job_id = str(job.get("id"))
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            postings.append(
                JobPosting(
                    source="jobicy",
                    external_id=job_id,
                    title=job.get("jobTitle", ""),
                    company=job.get("companyName", "Unknown"),
                    location=job.get("jobGeo", "Remote"),
                    remote=True,
                    description=job.get("jobExcerpt", "") or job.get("jobDescription", ""),
                    apply_url=job.get("url", ""),
                    posted_at=job.get("pubDate"),
                    date_source_reliability="aggregator",
                )
            )

    return postings
