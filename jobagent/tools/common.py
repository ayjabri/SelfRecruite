"""Shapes and helpers shared by every source tool."""
import hashlib
import logging
import math
import html
import re
from collections.abc import Callable
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from jobagent.db import session
from jobagent.models import Company, RawPosting

log = logging.getLogger(__name__)

MAX_IDS_RETURNED = 50  # summaries stay small enough for an LLM context


class FetchResult(BaseModel):
    """What every fetch_* tool returns: a summary, never the payloads."""
    source: str
    target: str
    fetched: int = 0
    inserted: int = 0
    already_present: int = 0
    filtered_out: int = 0    # seen but not stored (e.g. outside the US)
    raw_ids: list[int] = []  # newly inserted rows, capped at MAX_IDS_RETURNED
    error: str | None = None


class NormalizedPosting(BaseModel):
    """Source-independent view of a raw posting; extraction only ever sees this."""
    company: str
    title: str
    location_text: str = ""
    description_text: str = ""
    description_html: str = ""           # unescaped HTML, for section and bullet parsing
    employment_type: str | None = None   # as the source states it, e.g. "Full time"
    requisition_id: str | None = None
    url: str | None = None
    posted_at: datetime | None = None
    # structured compensation from the ATS, trusted over the LLM when present
    comp_min: float | None = None
    comp_max: float | None = None
    comp_period: str | None = None


class RawItem(BaseModel):
    external_id: str
    url: str | None
    payload: dict
    content_hash: str


# source name -> function turning that source's payload into a NormalizedPosting
NORMALIZERS: dict[str, Callable[[dict], NormalizedPosting]] = {}


def html_to_text(content: str) -> str:
    """Unescape (some feeds double-escape), strip tags, keep line breaks."""
    soup = BeautifulSoup(html.unescape(content or ""), "html.parser")
    return re.sub(r"\n\s*\n+", "\n", soup.get_text("\n")).strip()


def content_hash(text: str) -> str:
    """sha256 of the description with case and whitespace normalised."""
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().lower().encode()).hexdigest()


_PLACEHOLDER_REQ = re.compile(r"^(see\b|n/?a$|none$|tbd$|-+$|null$)|\s", re.I)


def clean_requisition_id(req: str | None) -> str | None:
    """Feeds sometimes put filler like 'See Opening ID' where a requisition ID belongs."""
    req = (req or "").strip()
    return None if not req or _PLACEHOLDER_REQ.search(req) else req


def known_external_ids(source: str, target: str) -> set[str]:
    """IDs already stored for this target, so sources that need a detail call per posting can skip them."""
    with session() as s:
        return set(s.scalars(select(RawPosting.external_id).where(
            RawPosting.source == source, RawPosting.target == target)))


def store_raw_postings(source: str, target: str, items: list[RawItem]) -> FetchResult:
    """Insert untouched payloads; the (source, external_id, content_hash) key makes reruns harmless."""
    result = FetchResult(source=source, target=target, fetched=len(items))
    if not items:
        log.info("%s %s: nothing new to store", source, target)
        return result
    rows = [dict(source=source, target=target, **i.model_dump()) for i in items]
    with session() as s, s.begin():
        stmt = (
            insert(RawPosting).values(rows)
            .on_conflict_do_nothing(index_elements=["source", "external_id", "content_hash"])
            .returning(RawPosting.id)
        )
        ids = sorted(s.scalars(stmt).all())
    result.inserted = len(ids)
    result.already_present = len(rows) - len(ids)
    result.raw_ids = ids[:MAX_IDS_RETURNED]
    log.info("%s %s: stored %d new postings", source, target, result.inserted)
    return result


_COMPANY_SUFFIX = re.compile(r"[,.]?\s+(inc|llc|ltd|corp|corporation|co|gmbh|plc)\.?$", re.I)


def norm_company(name: str) -> str:
    return _COMPANY_SUFFIX.sub("", name.strip()).lower()


def norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.lower()).strip()


def get_company(s, name: str, ats: str, slug: str | None) -> Company:
    """Find or create the company row inside the caller's session."""
    n = norm_company(name)
    c = s.scalar(select(Company).where(Company.name_norm == n))
    if not c:
        c = Company(name=name, name_norm=n, ats=ats, ats_slug=slug)
        s.add(c)
        s.flush()
    return c


def days_since(value, today=None) -> int | None:
    """Days between a posting date (ISO date/datetime string) and today; None if unparseable."""
    if not value:
        return None
    try:
        posted = datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None
    return ((today or datetime.now(timezone.utc).date()) - posted).days


def hours_since(value) -> float | None:
    """Hours between a posting timestamp and now; date-only values count from midnight UTC."""
    if not value:
        return None
    try:
        posted = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - posted).total_seconds() / 3600


def hours_to_days(hours: int | None) -> int | None:
    """Sources that only say 'posted today / yesterday' get the day count that covers the window."""
    return None if hours is None else math.ceil(hours / 24)
