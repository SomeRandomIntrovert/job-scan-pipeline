"""
Common schema every job source normalizes into, so Stage 1.5 onward never
has to know which source a posting came from.
"""

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class JobPosting:
    source: str  # "adzuna" | "remoteok" | "greenhouse:{company}" | "lever:{company}" | "ashby:{company}"
    external_id: str  # source's own ID, used for Supabase dedupe
    title: str
    company: str
    location: str
    remote: bool
    description: str  # full JD text, needed for Stage 1.5/2 downstream
    apply_url: str
    posted_at: Optional[str]  # ISO 8601 if the source provides it, else None
    date_source_reliability: str  # "direct" | "aggregator" — see README caveat

    def to_dict(self):
        return asdict(self)
