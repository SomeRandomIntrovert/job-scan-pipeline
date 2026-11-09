"""
Stage 1.5 + 1.6 combined -- RULE-BASED VERSION, no LLM/API calls at all.

Rationale for dropping the LLM here: your dealbreaker rules are explicit,
well-defined patterns (not open-ended reasoning), so keyword/regex
matching can approximate them well -- and it runs instantly, for free,
with zero rate limits. Stage 2 (actual JD-to-resume fit scoring) stays
on the LLM, since that genuinely needs semantic judgment regex can't
approximate.

Same conservative philosophy as the original LLM prompt: when a sentence
is ambiguous, DO NOT flag it. A missed flag costs you seeing one extra
posting; a false flag silently costs you a posting you wanted to see.

Known limitation, stated honestly: this will miss some genuinely
mandatory requirements phrased in unusual ways, and may occasionally
flag something that was actually soft language, if the sentence
structure doesn't match the patterns below. Spot-check a sample of
output/stage1_5_tagged.json against the real JD text periodically --
if you find a real miss, it's a one-line pattern addition, not a rebuild.
"""

import html as html_lib
import json
import os
import re
from datetime import datetime, timezone

from dateutil import parser as dateparser

INPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "stage1_raw.json")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "stage1_5_tagged.json")

NCR_CITIES = ["gurugram", "gurgaon", "delhi", "noida", "new delhi", "ncr"]

INDIA_MARKERS = [
    "india", "karnataka", "maharashtra", "tamil nadu", "telangana",
    "uttar pradesh", "haryana", "gujarat", "west bengal", "kerala",
    "rajasthan", "punjab", "bihar", "madhya pradesh", "andhra pradesh",
    "odisha", "assam", "jharkhand", "chhattisgarh", "uttarakhand",
    "himachal pradesh", "goa", "tripura", "meghalaya", "manipur",
    "nagaland", "mizoram", "sikkim", "arunachal pradesh", "chandigarh",
    "puducherry", "jammu", "kashmir", "ladakh",
]

TECHNICAL_DEGREE_PATTERNS = [
    r"computer science", r"\bcs degree\b", r"software engineering",
    r"computer engineering", r"\bmba\b", r"electrical engineering",
    r"\bengineering degree\b", r"information technology degree", r"\bit degree\b",
    r"technical degree",
]
PSYCH_ARTS_PATTERNS = [
    r"psychology", r"behavioral science", r"behavioural science",
    r"social science", r"\barts degree\b", r"humanities", r"\bhci\b",
    r"human[- ]computer interaction",
]
MANDATORY_WORDS = [
    "required", "requires", "must have", "must possess", "minimum of",
    "mandatory", "essential", "must hold", "is required", "are required",
]
SOFT_WORDS = [
    "preferred", "a plus", "nice to have", "bonus", "ideally",
    "advantageous", "or equivalent", "desirable", "plus but not required",
]

RANGE_EXPERIENCE_PATTERN = re.compile(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\+?\s*(?:years?|yrs?)", re.IGNORECASE)
SINGLE_EXPERIENCE_PATTERN = re.compile(r"(\d{1,2})\+?\s*(?:years?|yrs?)", re.IGNORECASE)
# CR-04: deterministic dealbreaker and soft-flag rules

SENIORITY_TITLE_PATTERN = re.compile(
    r"\b(senior|sr\.?|lead|manager|head|principal|director|vp|staff)\b",
    re.IGNORECASE,
)

EXCLUDED_REQUIRED_DEGREES = [
    r"\bMBA\b",
    r"\bCA\b",
    r"\bCFA\b",
    r"\bCPA\b",
    r"\bPh\.?D\.?\b",
    r"\bPhD\b",
]

EXCLUDED_REQUIRED_TOOLS = [
    "UiPath", "Actimize", "Falcon", "SAS", "FICO",
    "NICE", "BPMN", "process mining", "Alteryx", "KNIME",
]

SOFT_EXCLUDED_ROLE_FAMILIES = {
    "deep_ml": [
        "RAG pipeline", "MLOps", "model training",
        "deep learning engineer",
    ],
    "software_engineering": [
        "React", "Node.js", "TypeScript", "full stack",
        "Java", "Golang",
    ],
    "sales_marketing": [
        "sales", "business development", "growth marketing", "SEO",
    ],
    "market_research_core": [
        "market research consultant",
    ],
}

# Per your instruction, contract and fixed-term employment are allowed.
# Do not add contract/fixed-term terms to this list.
EXCLUDED_EMPLOYMENT_PATTERNS = [
    r"\bthird[- ]party payroll\b",
    r"\bconsultant[- ]contract\b",
    r"\bfreelance(?:r|rs)?\b",
    r"\bintern(?:ship)?\b",
]

PROXIMITY_WINDOW = 70  # chars checked around a match for mandatory/soft language --
                        # deliberately local rather than whole-sentence, since real JD
                        # text (esp. colon-delimited field listings with no punctuation)
                        # can run unrelated phrases together into one "sentence"


def _clip_window(text, start, end, max_radius=PROXIMITY_WINDOW):
    """Proximity window that respects real sentence punctuation as a hard
    boundary when present, and only falls back to a raw character radius
    when there's no punctuation to anchor on (e.g. colon-delimited JD
    fields with no periods at all)."""
    left_limit = max(0, start - max_radius)
    left_chunk = text[left_limit:start]
    left_matches = list(re.finditer(r"[.!?\n]", left_chunk))
    left_bound = left_limit + left_matches[-1].end() if left_matches else left_limit

    right_limit = min(len(text), end + max_radius)
    right_chunk = text[end:right_limit]
    right_match = re.search(r"[.!?\n]", right_chunk)
    right_bound = end + right_match.start() if right_match else right_limit

    return text[left_bound:right_bound]


def _has_nearby(text, start, end, words):
    window = _clip_window(text, start, end).lower()
    return any(w in window for w in words)

REMOTE_FLEX_PATTERNS = [
    r"remote candidates?\s*(?:are\s*)?(?:welcome|considered|accepted|eligible)",
    r"fully remote option", r"remote work\s*(?:is\s*)?available",
    r"open to remote", r"remote[- ]friendly", r"work from anywhere",
    r"remote applicants? (?:welcome|considered)",
]

WORK_AUTH_PATTERNS = [
    re.compile(r"must be authorized to work in (?:the\s*)?([a-zA-Z\s]{2,30}?)(?:\.|,|;|\n|$)", re.IGNORECASE),
    re.compile(r"requires? (?:existing |current )?work authorization (?:for|in) (?:the\s*)?([a-zA-Z\s]{2,30}?)(?:\.|,|;|\n|$)", re.IGNORECASE),
    re.compile(r"must have (?:valid |existing )?work authorization (?:for|in) (?:the\s*)?([a-zA-Z\s]{2,30}?)(?:\.|,|;|\n|$)", re.IGNORECASE),
]
SPONSOR_WORDS = ["sponsor", "sponsorship", "visa support", "will sponsor", "can sponsor"]


def split_sentences(text):
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def check_education(text):
    flags = []
    seen = set()
    for pattern in TECHNICAL_DEGREE_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            has_mandatory = _has_nearby(text, match.start(), match.end(), MANDATORY_WORDS)
            has_soft = _has_nearby(text, match.start(), match.end(), SOFT_WORDS)
            if has_mandatory and not has_soft:
                label = f"Requires: {match.group(0).lower()}"
                if label not in seen:
                    flags.append(label)
                    seen.add(label)

    for pattern in PSYCH_ARTS_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            has_soft = _has_nearby(text, match.start(), match.end(), SOFT_WORDS)
            if has_soft:
                label = "Boost: psych/arts/social-science preferred"
                if label not in seen:
                    flags.append(label)
                    seen.add(label)
                break

    return flags


def check_experience(text):
    """
    CR-04 experience rule:
    - Hard flag when a clear minimum requirement exceeds 4 years.
    - Allow postings explicitly mentioning 0-3 years.
    - Soft flag when experience wording is ambiguous.
    """
    text_lower = text.lower()

    # Explicit 0-3-year experience makes this posting eligible,
    # including a senior title paired with an early-career requirement.
    has_0_3_years = bool(
        re.search(
            r"\b0\s*(?:-|–|to)\s*3\s*\+?\s*(?:years?|yrs?)\b",
            text_lower,
        )
        or re.search(
            r"\b(?:0|1|2|3)\s*\+?\s*(?:years?|yrs?)\b",
            text_lower,
        )
    )

    if has_0_3_years:
        return []

    matches = list(RANGE_EXPERIENCE_PATTERN.finditer(text))
    matches += [
        m for m in SINGLE_EXPERIENCE_PATTERN.finditer(text)
        if not any(r.start() <= m.start() and r.end() >= m.end() for r in matches)
    ]

    if not matches:
        return []

    # The spec says to use the first number/range near years/yrs.
    match = min(matches, key=lambda m: m.start())

    if match.lastindex and match.lastindex >= 2:
        minimum_years = int(match.group(1))
        maximum_years = int(match.group(2))
    else:
        minimum_years = int(match.group(1))
        maximum_years = minimum_years

    if minimum_years > 4:
        return [f"Requires: {minimum_years}+ yrs experience"]

    # A 4-year range is not a hard dealbreaker under the >4 rule.
    # If the description contains confusing/multiple experience values,
    # flag softly for review rather than hard-excluding.
    if len(matches) > 1:
        return ["Soft: ambiguous experience requirement"]

    return []


INDIA_STATE_MARKERS = [
    "karnataka", "maharashtra", "tamil nadu", "telangana",
    "uttar pradesh", "haryana", "gujarat", "west bengal", "kerala",
    "rajasthan", "punjab", "bihar", "madhya pradesh", "andhra pradesh",
    "odisha", "assam", "jharkhand", "chhattisgarh", "uttarakhand",
    "himachal pradesh", "goa", "tripura", "meghalaya", "manipur",
    "nagaland", "mizoram", "sikkim", "arunachal pradesh", "chandigarh",
    "puducherry", "jammu", "kashmir", "ladakh",
]
INDIA_CITY_MARKERS = [
    "bangalore", "bengaluru", "mumbai", "chennai", "kolkata", "hyderabad",
    "pune", "ahmedabad", "jaipur", "surat", "lucknow", "kanpur", "nagpur",
    "indore", "thane", "bhopal", "visakhapatnam", "patna", "vadodara",
    "ghaziabad", "ludhiana", "agra", "nashik", "faridabad", "meerut",
    "rajkot", "kalyan", "vasai", "varanasi", "srinagar", "aurangabad",
    "dhanbad", "amritsar", "navi mumbai", "prayagraj", "allahabad",
    "ranchi", "howrah", "coimbatore", "jabalpur", "gwalior", "vijayawada",
    "jodhpur", "madurai", "raipur", "kota", "guwahati", "thiruvananthapuram",
    "mysore", "mysuru", "kochi", "cochin", "bhubaneswar", "ernakulam",
]
INDIA_MARKERS = INDIA_STATE_MARKERS + INDIA_CITY_MARKERS
INDIA_INCLUSIVE_REGION_MARKERS = ["apac", "asia pacific", "asia-pacific", "asia"]


def is_india_signal(location_lower):
    # "india" itself is checked with a WORD BOUNDARY, not a plain
    # substring -- "Indiana"/"Indianapolis" both contain the raw letters
    # "india" and would otherwise false-match as domestic.
    if re.search(r"\bindia\b", location_lower):
        return True
    return any(marker in location_lower for marker in INDIA_MARKERS) or \
           any(marker in location_lower for marker in INDIA_INCLUSIVE_REGION_MARKERS)


# Layer 1: comprehensive blacklist -- essentially every country/US state
# except India, plus common regions/major cities. Kept even though the
# whitelist below is the real safety net, because a confident, explicit
# match here is a stronger and faster signal than "didn't match India."
FOREIGN_COUNTRY_MARKERS = [
    "afghanistan", "albania", "algeria", "andorra", "angola", "argentina",
    "armenia", "australia", "austria", "azerbaijan", "bahamas", "bahrain",
    "bangladesh", "barbados", "belarus", "belgium", "belize", "benin",
    "bhutan", "bolivia", "bosnia", "herzegovina", "botswana", "brazil",
    "brunei", "bulgaria", "burkina faso", "burundi", "cambodia",
    "cameroon", "canada", "chad", "chile", "china", "colombia", "comoros",
    "congo", "costa rica", "croatia", "cuba", "cyprus", "czech republic",
    "czechia", "denmark", "djibouti", "dominica", "dominican republic",
    "ecuador", "egypt", "el salvador", "estonia", "eswatini", "ethiopia",
    "fiji", "finland", "france", "gabon", "gambia", "georgia", "germany",
    "ghana", "greece", "grenada", "guatemala", "guinea", "guyana",
    "haiti", "honduras", "hungary", "iceland", "indonesia", "iran",
    "iraq", "ireland", "israel", "italy", "jamaica", "japan", "jordan",
    "kazakhstan", "kenya", "kiribati", "kosovo", "kuwait", "kyrgyzstan",
    "laos", "latvia", "lebanon", "lesotho", "liberia", "libya",
    "liechtenstein", "lithuania", "luxembourg", "madagascar", "malawi",
    "malaysia", "maldives", "mali", "malta", "mauritania", "mauritius",
    "mexico", "moldova", "monaco", "mongolia", "montenegro", "morocco",
    "mozambique", "myanmar", "namibia", "nauru", "nepal", "netherlands",
    "new zealand", "nicaragua", "niger", "nigeria", "north korea",
    "north macedonia", "norway", "oman", "pakistan", "palau",
    "palestine", "panama", "papua new guinea", "paraguay", "peru",
    "philippines", "poland", "portugal", "qatar", "romania", "russia",
    "rwanda", "samoa", "san marino", "saudi arabia", "senegal", "serbia",
    "seychelles", "sierra leone", "singapore", "slovakia", "slovenia",
    "solomon islands", "somalia", "south africa", "south korea",
    "south sudan", "spain", "sri lanka", "sudan", "suriname", "sweden",
    "switzerland", "syria", "taiwan", "tajikistan", "tanzania",
    "thailand", "timor-leste", "togo", "tonga", "trinidad", "tobago",
    "tunisia", "turkey", "turkmenistan", "tuvalu", "uganda", "ukraine",
    "united arab emirates", "united kingdom", "united states", "uruguay",
    "uzbekistan", "vanuatu", "vatican", "venezuela", "vietnam", "yemen",
    "zambia", "zimbabwe",
    # Full US state names (2-letter codes handled separately below)
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee",
    "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming",
    # Major cities (helps when country/state name isn't spelled out)
    "san francisco", "los angeles", "seattle", "austin", "chicago",
    "boston", "denver", "atlanta", "miami", "london", "berlin", "paris",
    "madrid", "barcelona", "zurich", "geneva", "toronto", "vancouver",
    "sydney", "melbourne", "dublin", "amsterdam", "stockholm", "tokyo",
    "tel aviv", "tbilisi", "yerevan", "sofia", "belgrade", "warsaw",
    "prague", "budapest", "vienna", "lisbon", "athens", "helsinki",
    "oslo", "copenhagen", "manila", "jakarta", "bangkok", "hanoi",
    "seoul", "shanghai", "beijing", "hong kong", "dubai", "abu dhabi",
    "cape town", "johannesburg", "nairobi", "lagos", "cairo",
    "buenos aires", "sao paulo", "bogota", "lima", "santiago",
    # Regions/continents that exclude India
    "emea", "latam", "latin america", "europe", "africa", "middle east",
    "nordics", "nordic", "scandinavia", "dach", "benelux", "anz",
    "north america", "americas",
]
FOREIGN_WORD_MARKERS = [
    "us", "uk", "usa", "dc", "uae",
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo",
    "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or",
    "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy",
    # "in" excluded -- collides with India's ISO code, same reasoning as before
]


def is_foreign_signal(location_lower):
    if any(marker in location_lower for marker in FOREIGN_COUNTRY_MARKERS):
        return True
    return any(re.search(rf"\b{word}\b", location_lower) for word in FOREIGN_WORD_MARKERS)


def _normalize_location(location):
    """Some sources (observed: at least one RemoteOK posting) return
    location as a list rather than a plain string. Coerce defensively
    here, once, rather than assume every source's schema is consistent."""
    if isinstance(location, list):
        return ", ".join(str(x) for x in location if x)
    if location is None:
        return ""
    return str(location)


def check_location(location, remote, sentences, source=""):
    """Unified location dealbreaker check -- applies the same foreign-tie
    logic REGARDLESS of remote vs on-site status. A "Country (Remote)"
    label almost always means presence in that country is still required,
    so as of this version, sponsorship/remote-flexibility language no
    longer cancels a foreign-location hard flag -- presence is the real
    blocker, not visa status."""
    flags = []
    location = _normalize_location(location)
    location_lower = location.lower()
    full_text = " ".join(sentences).lower()
    has_remote_flex = any(re.search(pattern, full_text) for pattern in REMOTE_FLEX_PATTERNS)

    # Adzuna is hardcoded to the India-region endpoint (/jobs/in/search/1)
    # -- every Adzuna result is guaranteed India-based by construction.
    # Never apply the foreign check to this source, remote or not.
    is_guaranteed_india = source == "adzuna"

    # A bare "Remote" with no country/city/state attached carries no
    # foreign tie at all -- only flag when the location string names a
    # SPECIFIC place, which is the actual signal that presence is required.
    is_generic_remote_label = location_lower.strip() in ("remote", "fully remote", "100% remote", "")

    if is_guaranteed_india:
        if not remote and not any(city in location_lower for city in NCR_CITIES):
            flags.append(f"On-site: {location.strip() if location else 'unspecified location'}")
    elif any(city in location_lower for city in NCR_CITIES):
        pass  # NCR -- ideal, whether remote or on-site
    elif is_generic_remote_label:
        pass  # no location tie at all -- not disqualifying
    elif is_india_signal(location_lower):
        # WHITELIST layer checked FIRST: explicitly matches India (state,
        # city, or an India-inclusive region like APAC) -- safe. Checking
        # this before the blacklist means India always wins even in the
        # unlikely case a place name coincidentally trips a foreign marker.
        if not remote:
            flags.append(f"On-site: {location.strip() if location else 'unspecified location'}")
    elif is_foreign_signal(location_lower):
        # BLACKLIST layer: explicit, confident match against a known
        # country/state/city/region. Comprehensive as of this version
        # (all ~190 countries, all 50 US states, major cities, regions).
        loc_label = location.strip() if location else "unspecified foreign location"
        flags.append(f"Requires: presence in {loc_label} (outside India)")
        return flags
    else:
        # Matches NEITHER list -- genuinely unrecognized text. Default to
        # foreign per your explicit directive: a blacklist alone can never
        # be complete (multiple real misses already proved this -- Georgia,
        # Bulgaria, Serbia, full state names), so anything not explicitly
        # proven Indian is treated as a dealbreaker rather than assumed safe.
        loc_label = location.strip() if location else "unspecified foreign location"
        flags.append(f"Requires: presence in {loc_label} (outside India)")
        return flags  # disqualifying on its own -- skip the remote-flex tag below, it doesn't apply

    if has_remote_flex:
        flags.append("Remote option available")

    return flags


def check_work_auth(remote, sentences):
    """Text-based fallback: catches cases where the location FIELD is
    generic ("Remote") but the JD body explicitly states a country
    requirement. Per the same directive as check_location, sponsorship
    language no longer cancels this -- presence is the real blocker,
    not visa/authorization status."""
    flags = []
    if not remote:
        return flags

    for s in sentences:
        for pattern in WORK_AUTH_PATTERNS:
            match = pattern.search(s)
            if match:
                country = match.group(1).strip()
                flags.append(f"Requires: existing {country} work authorization")
                break

    return flags


def compute_age_bracket(posted_at):
    if not posted_at:
        return "unknown"
    try:
        dt = dateparser.parse(posted_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return "unknown"

    hours_ago = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    if hours_ago < 0:
        return "unknown"
    if hours_ago <= 24:
        return "0-24h"
    if hours_ago <= 72:
        return "24-72h"
    if hours_ago <= 168:
        return "72h-1wk"
    return ">1wk"


def strip_html(text):
    if not text:
        return ""
    # Decode entities FIRST (&lt;div&gt; -> <div>), so real tags are exposed
    # to the tag-stripping regex below. Greenhouse's `content` field in
    # particular stores tags as escaped entities, not literal markup --
    # skipping this step leaves raw &lt;/&gt;/&quot; noise in the text.
    text = html_lib.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tag_posting(posting):
    description = strip_html(posting.get("description", ""))
    title = str(posting.get("title", ""))
    full_text = f"{title}\n{description}"
    full_text_lower = full_text.lower()
    sentences = split_sentences(description)

    flags = []

    # Existing behavior
    flags.extend(check_education(description))
    flags.extend(check_experience(description))

    # CR-04: seniority title
    seniority_match = SENIORITY_TITLE_PATTERN.search(title)

    has_0_3_years = bool(
        re.search(
            r"\b(?:0\s*(?:-|–|to)\s*3|[0-3])\s*(?:years?|yrs?)\b",
            full_text_lower,
        )
    )

    if seniority_match and not has_0_3_years:
        flags.append(
            f"Requires: seniority title ({seniority_match.group(1)})"
        )

    # CR-04: excluded employment types
    for pattern in EXCLUDED_EMPLOYMENT_PATTERNS:
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            flags.append(f"Requires: excluded employment type ({match.group(0)})")

    # CR-04: mandatory excluded degrees
    for pattern in EXCLUDED_REQUIRED_DEGREES:
        match = re.search(pattern, full_text, re.IGNORECASE)

        if match and _has_nearby(
            full_text,
            match.start(),
            match.end(),
            MANDATORY_WORDS,
        ):
            flags.append(
                f"Requires: excluded degree ({match.group(0)})"
            )

    # CR-04: excluded tools — soft flag only
    for tool in EXCLUDED_REQUIRED_TOOLS:
        match = re.search(
            rf"\b{re.escape(tool)}\b",
            full_text,
            re.IGNORECASE,
        )

        if match and _has_nearby(
            full_text,
            match.start(),
            match.end(),
            MANDATORY_WORDS,
        ):
            flags.append(f"Soft: required excluded tool ({tool})")

    # CR-04: excluded role families — soft flags
    for category, terms in SOFT_EXCLUDED_ROLE_FAMILIES.items():
        for term in terms:
            if re.search(re.escape(term), full_text, re.IGNORECASE):
                flags.append(f"Soft: excluded role family ({category})")
                break

    # Existing location and work-authorization behavior
    flags.extend(
        check_location(
            posting.get("location", ""),
            posting.get("remote", False),
            sentences,
            posting.get("source", ""),
        )
    )
    flags.extend(
        check_work_auth(posting.get("remote", False), sentences)
    )

    # Remove duplicate flags while preserving order
    return list(dict.fromkeys(flags))


def main():
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        postings = json.load(f)

    print(f"Loaded {len(postings)} postings from Stage 1.")

    error_count = 0
    for p in postings:
        p["age_bracket"] = compute_age_bracket(p.get("posted_at"))
        try:
            p["flags"] = tag_posting(p)
        except Exception as e:
            # One malformed posting (unexpected field type, etc.) should
            # never take down the entire run -- log it, leave flags empty
            # for just that posting, and keep going. This is what should
            # have caught today's list-vs-string location crash gracefully
            # instead of stopping the whole script.
            print(f"[stage1.5] Error tagging posting {p.get('source')}|{p.get('external_id')}, leaving flags empty: {e}")
            p["flags"] = []
            error_count += 1

    if error_count:
        print(f"\n{error_count} posting(s) had a tagging error -- flagged empty rather than crashing. Worth a look if this number is more than a handful.")

    bracket_counts = {}
    for p in postings:
        bracket_counts[p["age_bracket"]] = bracket_counts.get(p["age_bracket"], 0) + 1
    print(f"Age brackets: {bracket_counts}")

    flag_counts = {}
    for p in postings:
        for flag in p["flags"]:
            key = flag.split(":")[0]
            flag_counts[key] = flag_counts.get(key, 0) + 1
    print(f"Flag type counts: {flag_counts}")

    tagged_count = sum(1 for p in postings if p["flags"])
    print(f"{tagged_count} of {len(postings)} postings received at least one flag.")

    hard_dealbreaker_count = sum(
        1 for p in postings if any(f.startswith("Requires:") for f in p["flags"])
    )
    print(f"{hard_dealbreaker_count} of {len(postings)} postings have a HARD dealbreaker "
          f"(will be SKIPPED by Stage 2, saving that many LLM calls' worth of work).")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(postings, f, indent=2)

    print(f"Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
