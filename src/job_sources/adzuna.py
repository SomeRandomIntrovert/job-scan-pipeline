"""
Adzuna source. Aggregator — posted dates can reflect re-crawl time, not
true original posting date, so every posting from here is tagged
date_source_reliability="aggregator".
"""

import os
import time

import requests

from .common import JobPosting

BASE_URL = "https://api.adzuna.com/v1/api/jobs/in/search/1"  # India results, page 1


def fetch(title_queries, tool_queries, results_per_query=20):
    app_id = os.environ["ADZUNA_APP_ID"]
    app_key = os.environ["ADZUNA_APP_KEY"]

    postings = []
    seen_ids = set()  # a single job can match MULTIPLE queries (e.g. its title
                       # matches one query, its description mentions a tool
                       # keyword matching another) -- without this, the same
                       # real posting gets added once per matching query,
                       # creating genuine duplicate entries in the output.
    all_queries = title_queries + tool_queries

    for i, query in enumerate(all_queries):
        if i > 0:
            time.sleep(1)  # courtesy delay -- avoids the 503 burst-rate errors seen on rapid-fire calls

        params = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": results_per_query,
            "what": query,
            "content-type": "application/json",
        }

        resp = None
        for attempt in range(2):  # one retry on transient 503s
            try:
                resp = requests.get(BASE_URL, params=params, timeout=30)
            except requests.exceptions.RequestException as e:
                print(f"[adzuna] query '{query}' network error, skipping: {e}")
                resp = None
                break

            if resp.status_code == 503 and attempt == 0:
                print(f"[adzuna] query '{query}' got 503, retrying once after a short pause...")
                time.sleep(3)
                continue
            break

        if resp is None:
            continue

        if resp.status_code != 200:
            print(f"[adzuna] query '{query}' failed: {resp.status_code} {resp.text[:200]}")
            continue

        data = resp.json()
        for item in data.get("results", []):
            item_id = str(item.get("id"))
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            postings.append(
                JobPosting(
                    source="adzuna",
                    external_id=item_id,
                    title=item.get("title", ""),
                    company=(item.get("company") or {}).get("display_name", "Unknown"),
                    location=(item.get("location") or {}).get("display_name", ""),
                    remote="remote" in (item.get("title", "") + item.get("description", "")).lower(),
                    description=item.get("description", ""),
                    apply_url=item.get("redirect_url", ""),
                    posted_at=item.get("created"),
                    date_source_reliability="aggregator",
                )
            )

    return postings
