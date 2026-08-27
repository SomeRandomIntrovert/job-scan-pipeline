"""
Tier 2 source: pulls FULL boards for each target company (no keyword
limit on this source — Stage 1.5/2 does the filtering downstream).

Reads config/company_boards.json. For "needs_verification" entries, tries
the guessed slug anyway and just logs a warning if it 404s, rather than
silently dropping the company -- so you find out from the run log which
guesses were wrong, instead of the company quietly vanishing forever.
"""

import json
import os
import requests

from .common import JobPosting

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "company_boards.json")


def _fetch_greenhouse(company, slug):
    try:
        resp = requests.get(GREENHOUSE_URL.format(slug=slug), timeout=30)
    except requests.exceptions.RequestException as e:
        print(f"[greenhouse] {company} (slug='{slug}') network error: {e}")
        return None

    if resp.status_code != 200:
        print(f"[greenhouse] {company} (slug='{slug}') failed: {resp.status_code}")
        return None  # slug likely wrong -- distinct from "valid slug, zero jobs"

    jobs = resp.json().get("jobs", [])
    postings = []
    for job in jobs:
        postings.append(
            JobPosting(
                source=f"greenhouse:{slug}",
                external_id=str(job.get("id")),
                title=job.get("title", ""),
                company=company,
                location=(job.get("location") or {}).get("name", ""),
                remote="remote" in (job.get("location") or {}).get("name", "").lower(),
                description=job.get("content", ""),
                apply_url=job.get("absolute_url", ""),
                posted_at=job.get("updated_at"),
                date_source_reliability="direct",
            )
        )
    return postings


def _fetch_lever(company, slug):
    try:
        resp = requests.get(LEVER_URL.format(slug=slug), timeout=30)
    except requests.exceptions.RequestException as e:
        print(f"[lever] {company} (slug='{slug}') network error: {e}")
        return None

    if resp.status_code != 200:
        print(f"[lever] {company} (slug='{slug}') failed: {resp.status_code}")
        return None

    jobs = resp.json()
    postings = []
    for job in jobs:
        categories = job.get("categories", {}) or {}
        postings.append(
            JobPosting(
                source=f"lever:{slug}",
                external_id=str(job.get("id")),
                title=job.get("text", ""),
                company=company,
                location=categories.get("location", ""),
                remote="remote" in (categories.get("location", "") or "").lower(),
                description=job.get("descriptionPlain", job.get("description", "")),
                apply_url=job.get("hostedUrl", ""),
                posted_at=None,  # Lever exposes createdAt as epoch ms; converted in Stage 1 orchestrator
                date_source_reliability="direct",
            )
        )
    return postings


def _fetch_ashby(company, slug):
    try:
        resp = requests.get(ASHBY_URL.format(slug=slug), timeout=30)
    except requests.exceptions.RequestException as e:
        print(f"[ashby] {company} (slug='{slug}') network error: {e}")
        return None

    if resp.status_code != 200:
        print(f"[ashby] {company} (slug='{slug}') failed: {resp.status_code}")
        return None  # slug likely wrong -- distinct from "valid slug, zero jobs"

    jobs = resp.json().get("jobs", [])
    postings = []
    for job in jobs:
        location = job.get("location", "") or ""
        postings.append(
            JobPosting(
                source=f"ashby:{slug}",
                external_id=str(job.get("id")),
                title=job.get("title", ""),
                company=company,
                location=location,
                remote=bool(job.get("isRemote", False)) or "remote" in location.lower(),
                description=job.get("descriptionPlain", ""),
                apply_url=job.get("jobUrl", ""),
                posted_at=job.get("publishedAt"),
                date_source_reliability="direct",
            )
        )
    return postings


def fetch():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    all_postings = []
    unresolved = []

    for entry in config.get("greenhouse_confirmed", []):
        result = _fetch_greenhouse(entry["company"], entry["slug"])
        if result is None:
            unresolved.append(f"{entry['company']} (previously-confirmed slug now failing!)")
        else:
            all_postings.extend(result)

    for entry in config.get("greenhouse_needs_verification", []):
        result = _fetch_greenhouse(entry["company"], entry["guessed_slug"])
        if result is None:
            unresolved.append(entry["company"])
        else:
            all_postings.extend(result)

    for entry in config.get("ashby_confirmed", []):
        result = _fetch_ashby(entry["company"], entry["slug"])
        if result is None:
            unresolved.append(f"{entry['company']} (previously-confirmed slug now failing!)")
        else:
            all_postings.extend(result)

    for entry in config.get("ashby_needs_verification", []):
        result = _fetch_ashby(entry["company"], entry["guessed_slug"])
        if result is None:
            unresolved.append(entry["company"])
        else:
            all_postings.extend(result)

    if unresolved:
        print(f"[ats_boards] Could not resolve slugs for: {', '.join(unresolved)}")
        print("These companies need their real slug looked up manually and added to company_boards.json.")

    return all_postings
