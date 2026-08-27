"""
WeWorkRemotely source. Public RSS feed, no key required. RSS gives no
structured JD body beyond a short HTML description, so postings from
here typically get thinner Stage 2 scoring context than API sources --
that's a real limitation, not a bug, and worth knowing if this source's
postings seem to score less confidently than others.
"""

import feedparser

from .common import JobPosting

FEED_URL = "https://weworkremotely.com/remote-jobs.rss"


def fetch(title_queries, tool_queries):
    try:
        feed = feedparser.parse(FEED_URL)
    except Exception as e:
        print(f"[weworkremotely] network/parse error, skipping source: {e}")
        return []

    if getattr(feed, "bozo", False):
        print(f"[weworkremotely] feed parse warning (may still have partial data): {feed.bozo_exception}")

    all_terms = [q.lower() for q in title_queries + tool_queries]
    postings = []

    for entry in feed.entries:
        title = entry.get("title", "")
        description = entry.get("summary", "")
        haystack = (title + " " + description).lower()

        if not any(term in haystack for term in all_terms):
            continue

        # WWR titles are typically "Company: Job Title"
        company = title.split(":", 1)[0].strip() if ":" in title else "Unknown"

        postings.append(
            JobPosting(
                source="weworkremotely",
                external_id=entry.get("id", entry.get("link", "")),
                title=title,
                company=company,
                location="Remote",
                remote=True,
                description=description,
                apply_url=entry.get("link", ""),
                posted_at=entry.get("published", None),
                date_source_reliability="aggregator",
            )
        )

    return postings
