"""Shapes and helpers shared by every source tool."""
import hashlib
import html
import re
from collections.abc import Callable
from datetime import datetime

from bs4 import BeautifulSoup
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert

from jobagent.db import session
from jobagent.models import RawPosting

MAX_IDS_RETURNED = 50  # summaries stay small enough for an LLM context


class FetchResult(BaseModel):
    """What every fetch_* tool returns: a summary, never the payloads."""
    source: str
    target: str
    fetched: int = 0
    inserted: int = 0
    already_present: int = 0
    raw_ids: list[int] = []  # newly inserted rows, capped at MAX_IDS_RETURNED
    error: str | None = None


class NormalizedPosting(BaseModel):
    """Source-independent view of a raw posting; extraction only ever sees this."""
    company: str
    title: str
    location_text: str = ""
    description_text: str = ""
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


def store_raw_postings(source: str, target: str, items: list[RawItem]) -> FetchResult:
    """Insert untouched payloads; the (source, external_id, content_hash) key makes reruns harmless."""
    result = FetchResult(source=source, target=target, fetched=len(items))
    if not items:
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
    return result
