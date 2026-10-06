"""Reads countries (and US states) out of free-text job locations.

`countries_in(text)` returns the ISO codes it recognises: "US" from state codes, state names
and US cities; other countries from their names, common codes and major cities. A location
nothing matches is "unknown", which is never filtered out: a place missing from these lists
costs an unnecessary parse, not a hidden job. `classify_location` keeps the older
us / non_us / unknown answer on top of that.
"""
import re

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia",
}
_US_PLACES = [
    "United States", "USA", "US", "U.S.", "D.C.", "NYC", "SF", "Bay Area", "Silicon Valley",
    "New York City", "San Francisco", "Seattle", "Chicago", "Boston", "Austin", "Dallas", "Houston",
    "Atlanta", "Denver", "Los Angeles", "San Diego", "San Jose", "Mountain View", "Palo Alto",
    "Santa Clara", "Sunnyvale", "Menlo Park", "Redmond", "Bellevue", "Portland", "Phoenix", "Miami",
    "Tampa", "Orlando", "Charlotte", "Raleigh", "Nashville", "Philadelphia", "Pittsburgh", "Detroit",
    "Minneapolis", "Salt Lake City", "Las Vegas", "Columbus", "Cleveland", "Cincinnati", "St. Louis",
    "Kansas City", "Jersey City", "Wilmington", "Plano", "Brooklyn", "Baltimore",
    *(n for n in STATES.values() if n != "Georgia"),  # Georgia is also a country; ", GA" still counts
]

# ISO code -> names, codes and well-known cities. Extend freely; unknown places are kept, not dropped.
COUNTRIES: dict[str, list[str]] = {
    "GB": ["United Kingdom", "UK", "Great Britain", "England", "Scotland", "Wales", "London", "Glasgow", "Edinburgh",
           "Manchester", "Birmingham", "Belfast", "Lanarkshire"],
    "IE": ["Ireland", "Dublin", "Cork"],
    "CA": ["Canada", "CAN", "Toronto", "Vancouver", "Montreal", "Montréal", "Ottawa", "Calgary", "Ontario", "Quebec",
           "Québec", "British Columbia", "Alberta"],
    "MX": ["Mexico", "México", "Mexico City", "Guadalajara", "Monterrey"],
    "BR": ["Brazil", "Brasil", "Sao Paulo", "São Paulo", "Rio de Janeiro"],
    "AR": ["Argentina", "Buenos Aires"], "CL": ["Chile", "Santiago"], "CO": ["Colombia", "Bogota", "Bogotá"],
    "PE": ["Peru", "Lima"], "DE": ["Germany", "Deutschland", "Berlin", "Munich", "München", "Frankfurt", "Hamburg"],
    "FR": ["France", "Paris", "Lyon"], "ES": ["Spain", "España", "Madrid", "Barcelona"],
    "PT": ["Portugal", "Lisbon", "Lisboa", "Porto"], "IT": ["Italy", "Italia", "Milan", "Milano", "Rome", "Roma"],
    "NL": ["Netherlands", "Amsterdam", "Rotterdam"], "BE": ["Belgium", "Brussels"], "LU": ["Luxembourg"],
    "CH": ["Switzerland", "Zurich", "Zürich", "Geneva", "Basel"], "AT": ["Austria", "Vienna", "Wien"],
    "SE": ["Sweden", "Stockholm"], "NO": ["Norway", "Oslo"], "DK": ["Denmark", "Copenhagen"],
    "FI": ["Finland", "Helsinki"], "PL": ["Poland", "Warsaw", "Krakow", "Kraków", "Wroclaw"],
    "RO": ["Romania", "Bucharest"], "RS": ["Serbia", "Belgrade"], "CZ": ["Czech Republic", "Czechia", "Prague"],
    "HU": ["Hungary", "Budapest"], "GR": ["Greece", "Athens"], "TR": ["Turkey", "Türkiye", "Istanbul"],
    "IL": ["Israel", "Tel Aviv"], "AE": ["UAE", "United Arab Emirates", "Dubai", "Abu Dhabi"],
    "SA": ["Saudi Arabia", "Riyadh"], "QA": ["Qatar", "Doha"], "EG": ["Egypt", "Cairo"],
    "NG": ["Nigeria", "Lagos"], "KE": ["Kenya", "Nairobi"], "ZA": ["South Africa", "Johannesburg", "Cape Town"],
    "IN": ["India", "Bengaluru", "Bangalore", "Mumbai", "Pune", "Hyderabad", "Chennai", "Gurgaon", "Gurugram", "Delhi",
           "New Delhi", "Noida", "Kolkata"],
    "CN": ["China", "Shanghai", "Beijing", "Shenzhen", "Guangzhou"], "HK": ["Hong Kong"],
    "JP": ["Japan", "Tokyo", "Osaka"], "KR": ["South Korea", "Korea", "Seoul"], "TW": ["Taiwan", "Taipei"],
    "SG": ["Singapore"], "TH": ["Thailand", "Bangkok"], "VN": ["Vietnam", "Ho Chi Minh City", "Hanoi"],
    "MY": ["Malaysia", "Kuala Lumpur"], "ID": ["Indonesia", "Jakarta"], "PH": ["Philippines", "Manila"],
    "AU": ["Australia", "Sydney", "Melbourne", "Brisbane"], "NZ": ["New Zealand", "Auckland", "Wellington"],
}
_REGIONS = ["EMEA", "APAC", "LATAM", "EU", "Europe", "Asia"]   # non-US, but no single country
_NON_US_PLACES = [*_REGIONS, *(p for names in COUNTRIES.values() for p in names)]
_COUNTRY_NAMES = {n.lower(): code for code, names in COUNTRIES.items() for n in names}
_COUNTRY_NAMES.update({code.lower(): code for code in COUNTRIES if code not in ("CA", "CO", "IN", "DE", "IL", "ID", "AR", "PE", "MY")})  # bare codes that are not also US states


def phrase_regex(phrases) -> re.Pattern | None:
    """Whole-word, case-insensitive match for any of the phrases."""
    phrases = [p.strip() for p in phrases if p and p.strip()]
    if not phrases:
        return None
    alt = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alt})(?![A-Za-z0-9])", re.I)


# state codes are case-sensitive and only trusted in "City, CA" or "US, CA, City" positions
_STATE_CODE = re.compile(rf"(?:,\s*|-\s*|^)(?:{'|'.join(STATES)})(?![A-Za-z])")
_SPLIT = re.compile(r"\s*(?:;|\||/|\bor\b|\bOR\b)\s*")
_US_RE = phrase_regex(_US_PLACES)
_NON_US_RE = phrase_regex(_NON_US_PLACES)


def country_code(name: str | None) -> str | None:
    """'South Korea' -> 'KR', 'US' -> 'US'; None when not recognised."""
    if not name:
        return None
    n = name.strip()
    if n.upper() == "US" or n.lower() in ("usa", "u.s.", "united states", "united states of america"):
        return "US"
    return _COUNTRY_NAMES.get(n.lower()) or (n.upper() if n.upper() in COUNTRIES else None)


def countries_in(text: str, extra_us=(), extra_non_us=()) -> set[str]:
    """ISO codes of every country the text names; empty when nothing is recognised."""
    us_re = phrase_regex([*_US_PLACES, *extra_us]) if extra_us else _US_RE
    found: set[str] = set()
    for segment in _SPLIT.split(text or ""):
        foreign = {_COUNTRY_NAMES[m.group(0).lower()] for m in _NON_US_RE.finditer(segment) if m.group(0).lower() in _COUNTRY_NAMES}
        if extra_non_us and phrase_regex(extra_non_us).search(segment):
            foreign.add("??")
        found |= foreign
        # "Tel Aviv, IL" / "Toronto, CA": the city decided; a state code next to a foreign place is not a US state
        if us_re.search(segment) or (_STATE_CODE.search(segment) and not foreign):
            found.add("US")
    return found


def classify_location(text: str, extra_us=(), extra_non_us=()) -> tuple[str, str | None]:
    """Returns ("us" | "non_us" | "unknown", the non-US place that decided it)."""
    us_re = phrase_regex([*_US_PLACES, *extra_us]) if extra_us else _US_RE
    non_us_re = phrase_regex([*_NON_US_PLACES, *extra_non_us]) if extra_non_us else _NON_US_RE
    foreign = None
    for segment in _SPLIT.split(text or ""):
        hit = non_us_re.search(segment)
        if us_re.search(segment):
            return "us", None
        if hit:
            foreign = foreign or hit.group(0)
        elif _STATE_CODE.search(segment):  # "Tel Aviv, IL" is caught above; "Chicago, IL" lands here
            return "us", None
    return ("non_us", foreign) if foreign else ("unknown", None)


def allowed_by_country(text: str, allowed: set[str], extra_us=(), extra_non_us=()) -> tuple[bool, str | None]:
    """(keep?, the place that decided). Kept when any recognised country is allowed, or none is recognised."""
    if not allowed:
        return True, None
    where, place = classify_location(text, extra_us, extra_non_us)
    if "US" in allowed and where == "us":
        return True, None
    found = countries_in(text, extra_us, extra_non_us)
    if found & allowed:
        return True, None
    if found or where == "non_us":
        return False, place or ", ".join(sorted(found))
    return True, None
