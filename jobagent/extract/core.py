"""LLM extraction of one posting, plus the guardrails against invented fields."""
import re

from pydantic import ValidationError

from jobagent.extract.schema import JobExtract
from jobagent.llm import get_chat_model
from jobagent.tools.common import NormalizedPosting

SYSTEM = (
    "You extract structured data from a job posting. Use only what the posting says. "
    "If a field is not stated, use null (or 'unknown'/'unstated' where the schema allows). "
    "Never guess salary figures: comp_text must be an exact sentence copied from the posting "
    "and comp_min/comp_max must be numbers that appear in it. "
    "workplace_type: use 'remote' or 'hybrid' only if the posting or its location says so, else 'unknown'. "
    "Keep each list item short. 'summary' is 2-3 sentences in your own words."
)

_TITLE_LEVELS = [  # first match wins
    ("vp_plus", r"\b(vp|svp|evp|vice president|chief|head of)\b"),
    ("director", r"\bdirector\b"),
    ("lead", r"\b(staff|principal|lead|manager)\b"),
    ("senior", r"\b(senior|sr)\b"),
    ("entry", r"\b(intern|internship|junior|jr|entry[- ]level|new grad|graduate)\b"),
]


def derive_seniority(title: str, min_years: int | None, model_value: str) -> str:
    """Title keywords first, then required years; the model's answer is only the fallback."""
    for level, pattern in _TITLE_LEVELS:
        if re.search(pattern, title, re.I):
            return level
    if min_years is not None:
        return "entry" if min_years < 2 else "mid" if min_years < 5 else "senior"
    return model_value


def _norm(s: str) -> str:
    return re.sub(r"[\s ]+", " ", s.replace("’", "'").replace("–", "-")).lower().strip()


def _numbers_in(s: str) -> set[float]:
    return {float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", s)}


def apply_guardrails(ex: JobExtract, posting: NormalizedPosting) -> JobExtract:
    """Drop fields the posting doesn't support, and overwrite what code knows better."""
    text = posting.description_text
    haystack = f"{text} {posting.location_text}".lower()
    if ex.workplace_type in ("remote", "hybrid") and ex.workplace_type not in haystack:
        ex.workplace_type = "unknown"

    if posting.comp_min is not None and posting.comp_max is not None:
        # structured compensation from the ATS wins over anything the model read
        ex.comp_min, ex.comp_max = posting.comp_min, posting.comp_max
        ex.comp_period, ex.comp_text = posting.comp_period or "unknown", None
    else:
        ok = False
        if ex.comp_text and _norm(ex.comp_text) in _norm(text):
            nums = _numbers_in(ex.comp_text)
            def present(v):  # also accept "$120K" for 120000
                return v is None or v in nums or (v % 1000 == 0 and v / 1000 in nums)
            ok = (ex.comp_min is not None or ex.comp_max is not None) and present(ex.comp_min) and present(ex.comp_max)
        if not ok:
            ex.comp_min = ex.comp_max = ex.comp_text = None
            ex.comp_period = "unknown"

    ex.seniority = derive_seniority(posting.title, ex.min_years_experience, ex.seniority)
    ex.job_title = posting.title
    ex.company = posting.company
    ex.requisition_id = posting.requisition_id
    return ex


def llm_extract(posting: NormalizedPosting) -> JobExtract:
    """One retry on invalid output, then raise."""
    model = get_chat_model().with_structured_output(JobExtract)
    messages = [
        ("system", SYSTEM),
        ("user", f"Company (from ATS): {posting.company}\nTitle (from ATS): {posting.title}\n"
                 f"Location (from ATS): {posting.location_text}\n\nPOSTING:\n{posting.description_text}"),
    ]
    last: Exception | None = None
    for _ in range(2):
        try:
            return apply_guardrails(model.invoke(messages), posting)
        except (ValidationError, ValueError) as e:
            last = e
    raise last
