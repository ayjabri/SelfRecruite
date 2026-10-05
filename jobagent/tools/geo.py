"""Decides whether a free-text job location is in the United States.

Three answers: "us", "non_us" or "unknown". Unknown is never filtered out, so a
place missing from these lists costs an unnecessary extraction, not a hidden job.
"""
import re

_STATES = {
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
    *(n for n in _STATES.values() if n != "Georgia"),  # Georgia is also a country; ", GA" still counts
]
_NON_US_PLACES = [
    # regions and codes
    "UK", "EMEA", "APAC", "LATAM", "EU", "Europe", "Asia",
    # countries
    "United Kingdom", "England", "Scotland", "Ireland", "Canada", "CAN", "Mexico", "Brazil", "Argentina",
    "Chile", "Colombia", "Peru", "Germany", "France", "Spain", "Portugal", "Italy", "Netherlands",
    "Belgium", "Luxembourg", "Switzerland", "Austria", "Sweden", "Norway", "Denmark", "Finland",
    "Poland", "Romania", "Serbia", "Czech Republic", "Czechia", "Hungary", "Greece", "Turkey",
    "Israel", "UAE", "United Arab Emirates", "Saudi Arabia", "Qatar", "Egypt", "Nigeria", "Kenya",
    "South Africa", "India", "China", "Japan", "South Korea", "Korea", "Singapore", "Hong Kong",
    "Taiwan", "Thailand", "Vietnam", "Malaysia", "Indonesia", "Philippines", "Australia", "New Zealand",
    # cities and provinces
    "London", "Dublin", "Glasgow", "Edinburgh", "Manchester", "Toronto", "Vancouver", "Montreal",
    "Ontario", "Quebec", "British Columbia", "Mexico City", "Sao Paulo", "São Paulo", "Bogota",
    "Bogotá", "Buenos Aires", "Santiago", "Lima", "Paris", "Berlin", "Munich", "Frankfurt", "Hamburg",
    "Amsterdam", "Brussels", "Zurich", "Geneva", "Vienna", "Madrid", "Barcelona", "Lisbon", "Milan",
    "Rome", "Stockholm", "Oslo", "Copenhagen", "Helsinki", "Warsaw", "Krakow", "Bucharest", "Belgrade",
    "Prague", "Budapest", "Tel Aviv", "Dubai", "Riyadh", "Bengaluru", "Bangalore", "Mumbai", "Pune",
    "Hyderabad", "Chennai", "Gurgaon", "Gurugram", "Delhi", "Noida", "Shanghai", "Beijing", "Shenzhen",
    "Tokyo", "Osaka", "Seoul", "Taipei", "Bangkok", "Jakarta", "Manila", "Kuala Lumpur", "Sydney",
    "Melbourne", "Auckland",
]


def phrase_regex(phrases) -> re.Pattern | None:
    """Whole-word, case-insensitive match for any of the phrases."""
    phrases = [p.strip() for p in phrases if p and p.strip()]
    if not phrases:
        return None
    alt = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alt})(?![A-Za-z0-9])", re.I)


# state codes are case-sensitive and only trusted in "City, CA" or "US, CA, City" positions
_STATE_CODE = re.compile(rf"(?:,\s*|-\s*|^)(?:{'|'.join(_STATES)})(?![A-Za-z])")
_SPLIT = re.compile(r"\s*(?:;|\||/|\bor\b|\bOR\b)\s*")


def classify_location(text: str, extra_us=(), extra_non_us=()) -> tuple[str, str | None]:
    """Returns ("us" | "non_us" | "unknown", the non-US place that decided it)."""
    us_re = phrase_regex([*_US_PLACES, *extra_us])
    non_us_re = phrase_regex([*_NON_US_PLACES, *extra_non_us])
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
