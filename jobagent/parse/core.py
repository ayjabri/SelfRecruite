"""Rule-based parsing of one posting. No LLM, no network, no database."""
import re

from bs4 import BeautifulSoup

from jobagent.config import load_targets
from jobagent.extract.core import derive_seniority
from jobagent.extract.schema import Location
from jobagent.parse.schema import ParsedJob, Section
from jobagent.tools.common import NormalizedPosting
from jobagent.tools.geo import STATES, classify_location

# --- sections -----------------------------------------------------------------------------

_HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
_MAX_HEADING = 90
# checked in this order: "preferred qualifications" must not be read as "qualifications"
_LABELS = [
    ("preferred", r"prefer|nice[- ]to[- ]have|bonus|\bplus\b|stand out|strong candidates|desired|"
                  r"ideal|good to have|extra credit|even better"),
    ("responsibilities", r"responsib|what you(?:'|’)?ll (?:do|be doing|work on|own)|what you will (?:do|be doing)|"
                         r"what you(?:'|’)?ll be|the role|about the role|duties|your impact|in this role|"
                         r"day[- ]to[- ]day|you will\b|you(?:'|’)ll\b|job description|what you get to do"),
    ("required", r"requirement|qualif|what you(?:'|’)?ll need|what you will need|what we(?:'|’)?re looking for|"
                 r"what we are looking for|who you are|you have\b|good fit|must[- ]have|minimum|"
                 r"what you bring|what you(?:'|’)?ll bring|about you|need to see|skills|experience|"
                 r"what we look for|\brequired\b|"
                 r"you might be|you may be|we(?:'|’)?d love|looking for"),
]


def label_heading(heading: str) -> str:
    for label, pattern in _LABELS:
        if re.search(pattern, heading, re.I):
            return label
    return "other"


_TYPED_BULLET = re.compile(r"^[•·▪●◦■\-–—\*]\s+(\S.*)$")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip()


def _is_heading(tag) -> str | None:
    """The heading text if this tag acts as a section heading, else None."""
    text = _clean(tag.get_text(" "))
    if not text or len(text) > _MAX_HEADING or tag.find_parent("li"):
        return None
    if tag.name in _HEADING_TAGS:
        return text
    if tag.name in ("p", "div"):
        if tag.find(["p", "div", "ul", "ol", "li"]):
            return None
        bold = _clean(" ".join(b.get_text(" ") for b in tag.find_all(["b", "strong"])))
        letters = re.sub(r"[^A-Za-z]", "", text)
        shouting = len(letters) >= 6 and letters.isupper()  # "KEY RESPONSIBILITIES"
        if text.endswith(":") or (bold and bold == text) or shouting:
            return text
    if tag.name in ("b", "strong", "u"):
        parent = tag.find_parent(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6"])
        if parent is not None and _clean(parent.get_text(" ")) == text:
            return None  # the parent block is the heading; avoid counting it twice
        if text.endswith(":"):
            return text
    return None


def parse_sections(description_html: str) -> list[Section]:
    soup = BeautifulSoup(description_html or "", "html.parser")
    sections: list[Section] = []
    current = Section(heading="", label="other", bullets=[])
    for tag in soup.find_all([*_HEADING_TAGS, "p", "div", "b", "strong", "u", "li"]):
        if tag.name == "li":
            if tag.find("li"):
                continue  # a parent of nested bullets; the leaves carry the text
            text = _clean(tag.get_text(" "))
            if text:
                current.bullets.append(text)
            continue
        if tag.name in ("p", "div") and not tag.find(["p", "div", "ul", "ol", "li"]):
            text = _clean(tag.get_text(" "))
            typed = _TYPED_BULLET.match(text)  # some sites type "• text" in paragraphs instead of lists
            if typed:
                current.bullets.append(typed.group(1))
                continue
        heading = _is_heading(tag)
        if heading and heading != current.heading:
            if current.bullets:
                sections.append(current)
            current = Section(heading=heading.rstrip(":").strip(), label=label_heading(heading), bullets=[])
    if current.bullets:
        sections.append(current)
    return sections


# --- compensation -------------------------------------------------------------------------

_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?\s?[kK]\b|\d+(?:\.\d+)?)"
_RANGE = re.compile(
    rf"(?P<c1>\$|USD\s?)?\s?{_NUM}\s?(?P<c2>USD)?\s*(?:-|–|—|to)\s*(?P<c3>\$|USD\s?)?\s?{_NUM}\s?(?P<c4>USD)?", re.I
)


def _to_number(s: str) -> float:
    s = s.replace(",", "").strip()
    return float(s[:-1]) * 1000 if s[-1] in "kK" else float(s)


def parse_compensation(text: str) -> tuple[float | None, float | None, str, str | None]:
    """(min, max, period, source sentence). Several ranges (e.g. per level) are merged."""
    yearly, hourly, first = [], [], None
    for m in _RANGE.finditer(text):
        if not any(m.group(g) for g in ("c1", "c2", "c3", "c4")):
            continue  # a bare "3 - 5" is not money
        lo, hi = _to_number(m.group(2)), _to_number(m.group(5))
        if lo > hi:
            continue
        around = text[max(0, m.start() - 60): m.end() + 60].lower()
        if lo >= 10_000:
            yearly.append((lo, hi))
        elif hi < 1_000 and re.search(r"hour|/\s?hr\b|hourly", around):
            hourly.append((lo, hi))
        else:
            continue
        first = first or m
    ranges, period = (yearly, "year") if yearly else (hourly, "hour")
    if not ranges:
        return None, None, "unknown", None
    start = max(text.rfind(".", 0, first.start()), text.rfind("\n", 0, first.start())) + 1
    ends = [i for i in (text.find(". ", first.end()), text.find("\n", first.end())) if i != -1]
    sentence = _clean(text[start: min(ends) + 1 if ends else len(text)])[:400]
    return min(r[0] for r in ranges), max(r[1] for r in ranges), period, sentence


# --- small fields -------------------------------------------------------------------------

_YEARS = re.compile(r"(\d{1,2})\s?\+?\s*(?:(?:-|–|to)\s?\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b", re.I)


def parse_min_years(required_bullets: list[str], text: str) -> int | None:
    """Overall years asked for. Postings say "10+ years, including 3+ managing teams", so within
    the requirements the largest figure is the headline; outside them, trust only the first mention."""
    found = [int(m.group(1)) for m in _YEARS.finditer("\n".join(required_bullets)) if 0 < int(m.group(1)) <= 25]
    if found:
        return max(found)
    first = next((int(m.group(1)) for m in _YEARS.finditer(text) if 0 < int(m.group(1)) <= 25), None)
    return first


def parse_workplace(location_text: str, text: str) -> str:
    if re.search(r"\bremote\b", location_text, re.I):
        return "remote"
    if re.search(r"\bhybrid\b", f"{location_text} {text}", re.I):
        return "hybrid"
    if re.search(r"\b(fully remote|remote[- ]first|work remotely|remote position|remote role|this role is remote)\b", text, re.I):
        return "remote"
    if re.search(r"\b(on[- ]?site|in[- ]office|in the office)\b", text, re.I):
        return "onsite"
    return "unknown"


def parse_visa(text: str) -> str:
    if re.search(r"(not|unable to|cannot|can't|won't|will not|no|without)\b[^.]{0,60}\bsponsor", text, re.I):
        return "no"
    if re.search(r"(sponsorship (is )?available|will sponsor|we (do )?sponsor|able to sponsor|offer[^.]{0,30}sponsorship)", text, re.I):
        return "yes"
    return "unstated"


def parse_clearance(text: str) -> bool:
    return bool(re.search(r"\b(security|secret|top secret|ts/sci|government|dod)\s+clearance\b|\bTS/SCI\b", text, re.I))


_DEGREES = [
    ("phd", r"\bph\.?\s?d\b|\bdoctorate\b|\bdoctoral\b"),
    ("mba", r"\bmba\b"),
    ("master", r"\bmaster(?:'|’)?s?\b|\bm\.?s\.?\b(?= degree| in )|\bmsc\b"),
    ("bachelor", r"\bbachelor(?:'|’)?s?\b|\bb\.?s\.?\b(?= degree| in |/)|\bb\.?a\.?\b(?= degree| in |/)|\bundergraduate degree\b"),
]


def parse_education(text: str) -> list[str]:
    return [name for name, pattern in _DEGREES if re.search(pattern, text, re.I)]


_EMPLOYMENT = [
    ("part_time", r"part[- ]time"),
    ("contract", r"\bcontract(or)?\b|fixed[- ]term|temporary|\btemp\b"),
    ("full_time", r"full[- ]time"),
]


def parse_employment_type(source_value: str | None, title: str) -> str:
    for candidate in (source_value or "", title):
        for name, pattern in _EMPLOYMENT:
            if re.search(pattern, candidate, re.I):
                return name
    return "unknown"


_STATE_NAMES = {name.lower(): code for code, name in STATES.items()}
_US_WORDS = {"us", "usa", "u.s.", "united states", "united states of america"}


def parse_locations(location_text: str) -> list[Location]:
    """Best-effort split of 'City, ST | City, ST, Country' into parts."""
    out: list[Location] = []
    for segment in re.split(r"\s*(?:;|\|)\s*", location_text or ""):
        parts = [p.strip() for p in segment.split(",") if p.strip()]
        if not parts:
            continue
        where, _ = classify_location(segment)
        city = state = None
        country = "US" if where == "us" else None
        rest = []
        for p in parts:
            if p.lower() in _US_WORDS:
                country = "US"
            elif p.upper() in STATES and p.isupper() and where == "us":
                state = p
            elif p.lower() in _STATE_NAMES and where == "us" and len(parts) > 1:
                state = _STATE_NAMES[p.lower()]
            else:
                rest.append(p)
        if where != "us" and len(rest) > 1:
            country = rest.pop()
        if rest and not re.search(r"\bremote\b", rest[0], re.I):
            city = rest[0]
        if city or state or country:
            out.append(Location(city=city, state=state, country=country))
    return out


# --- the whole posting --------------------------------------------------------------------

def parse_posting(posting: NormalizedPosting) -> ParsedJob:
    text = posting.description_text
    sections = parse_sections(posting.description_html)
    labelled = any(s.label in ("required", "preferred") for s in sections)
    job = ParsedJob(
        sections=sections,
        sections_quality="labelled" if labelled else "unlabelled" if sections else "none",
        locations=parse_locations(posting.location_text),
        workplace_type=parse_workplace(posting.location_text, text),
        employment_type=parse_employment_type(posting.employment_type, posting.title),
        education=parse_education(text),
        visa_sponsorship=parse_visa(text),
        clearance_required=parse_clearance(text),
    )
    job.min_years_experience = parse_min_years(job.requirements, text)
    bank = posting.company.lower() in {c.lower() for c in load_targets().get("bank_title_companies") or []}
    job.seniority = derive_seniority(posting.title, job.min_years_experience, "unknown", bank=bank)
    if posting.comp_min is not None and posting.comp_max is not None:  # structured pay from the ATS wins
        job.comp_min, job.comp_max, job.comp_period = posting.comp_min, posting.comp_max, posting.comp_period or "unknown"
    else:
        job.comp_min, job.comp_max, job.comp_period, job.comp_text = parse_compensation(text)
    return job
