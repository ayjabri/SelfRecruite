"""Extraction tools: turn stored raw postings into jobs rows."""
import logging
import re
import time
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from jobagent.db import session
from jobagent.extract.core import llm_extract
from jobagent.models import Company, Job, JobSource, RawPosting
from jobagent.tools.common import MAX_IDS_RETURNED, NORMALIZERS
from jobagent.tools.registry import tool_spec

log = logging.getLogger(__name__)

_SUFFIX = re.compile(r"[,.]?\s+(inc|llc|ltd|corp|corporation|co|gmbh|plc)\.?$", re.I)


def norm_company(name: str) -> str:
    return _SUFFIX.sub("", name.strip()).lower()


def norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.lower()).strip()


def _pending_ids(s, limit: int | None) -> list[int]:
    linked = select(JobSource.raw_id).where(JobSource.raw_id == RawPosting.id).exists()
    return list(s.scalars(select(RawPosting.id).where(~linked).order_by(RawPosting.id).limit(limit)))


def _get_company(s, name: str, ats: str, slug: str | None) -> Company:
    n = norm_company(name)
    c = s.scalar(select(Company).where(Company.name_norm == n))
    if not c:
        c = Company(name=name, name_norm=n, ats=ats, ats_slug=slug)
        s.add(c)
        s.flush()
    return c


class ListPendingInput(BaseModel):
    limit: int | None = Field(None, ge=1, description="Maximum IDs to return; omit for the default cap")


class PendingResult(BaseModel):
    pending: int
    raw_ids: list[int]


@tool_spec(
    name="list_pending_extractions",
    description="Count the raw postings that have not been extracted into a job yet, and list their IDs.",
)
def list_pending_extractions(args: ListPendingInput) -> PendingResult:
    with session() as s:
        ids = _pending_ids(s, None)
    return PendingResult(pending=len(ids), raw_ids=ids[: args.limit or MAX_IDS_RETURNED])


class ExtractPostingInput(BaseModel):
    raw_id: int = Field(description="raw_postings.id to extract")


class ExtractResult(BaseModel):
    raw_id: int
    outcome: Literal["extracted", "cached", "linked", "skipped", "failed"]
    job_id: int | None = None
    title: str | None = None
    error: str | None = None


@tool_spec(
    name="extract_posting",
    description=(
        "Extract one raw posting into a structured jobs row using the LLM. Reuses an earlier "
        "extraction when the text is identical, and links to an existing job when the company "
        "and requisition ID match. Does nothing if the posting was already processed."
    ),
)
def extract_posting(args: ExtractPostingInput) -> ExtractResult:
    # 1. read everything needed, then release the connection before the slow LLM call
    with session() as s:
        raw = s.get(RawPosting, args.raw_id)
        if raw is None:
            return ExtractResult(raw_id=args.raw_id, outcome="failed", error="no such raw posting")
        if s.scalar(select(JobSource.job_id).where(JobSource.raw_id == raw.id)):
            return ExtractResult(raw_id=raw.id, outcome="skipped")
        normalizer = NORMALIZERS.get(raw.source)
        if normalizer is None:
            return ExtractResult(raw_id=raw.id, outcome="failed", error=f"no normalizer for source '{raw.source}'")
        posting = normalizer(raw.payload)
        source, target, chash = raw.source, raw.target, raw.content_hash
        if posting.requisition_id:  # same company + requisition = same opening: link, skip the LLM
            existing = s.scalar(
                select(Job.id).join(Company, Company.id == Job.company_id).where(
                    Company.name_norm == norm_company(posting.company),
                    Job.requisition_id == posting.requisition_id)
            )
            if existing:
                s.add(JobSource(job_id=existing, raw_id=raw.id))
                s.commit()
                return ExtractResult(raw_id=args.raw_id, outcome="linked", job_id=existing, title=posting.title)
        cached = s.scalar(
            select(Job.extracted).join(JobSource, JobSource.job_id == Job.id)
            .join(RawPosting, RawPosting.id == JobSource.raw_id)
            .where(RawPosting.content_hash == chash, Job.status != "extract_failed").limit(1)
        )

    # 2. extract (no DB connection held)
    outcome, extracted, error = "cached", cached, None
    if cached is None:
        try:
            extracted, outcome = llm_extract(posting).model_dump(), "extracted"
        except Exception as e:  # noqa: BLE001 - one bad posting must not block the batch
            outcome, error = "failed", f"{type(e).__name__}: {e}"[:500]

    # 3. write
    with session() as s, s.begin():
        company = _get_company(s, posting.company, source, target)
        job = Job(
            company_id=company.id, requisition_id=posting.requisition_id,
            title=posting.title, title_norm=norm_title(posting.title),
            description_text=posting.description_text, apply_url=posting.url,
            source_posted_at=posting.posted_at, extracted={"error": error},
            status="extract_failed",
        )
        if extracted is not None:
            job.extracted = extracted
            job.locations = extracted["locations"]
            job.workplace_type = extracted["workplace_type"]
            job.seniority = extracted["seniority"]
            job.comp_min, job.comp_max = extracted["comp_min"], extracted["comp_max"]
            job.comp_period = extracted["comp_period"]
            job.status = "extracted"
        s.add(job)
        s.flush()
        s.add(JobSource(job_id=job.id, raw_id=args.raw_id))
        return ExtractResult(raw_id=args.raw_id, outcome=outcome, job_id=job.id, title=posting.title, error=error)


class ExtractPendingInput(BaseModel):
    limit: int | None = Field(None, ge=1, description="Maximum postings to process in this call; omit for all")


class ExtractBatchResult(BaseModel):
    processed: int = 0
    extracted: int = 0
    cached: int = 0
    linked: int = 0
    skipped: int = 0
    failed: int = 0
    remaining: int = 0
    failed_raw_ids: list[int] = []


@tool_spec(
    name="extract_pending_postings",
    description=(
        "Extract every raw posting that has no job yet (optionally up to a limit). "
        "Returns counts per outcome, how many are still pending, and the IDs that failed."
    ),
)
def extract_pending_postings(args: ExtractPendingInput) -> ExtractBatchResult:
    with session() as s:
        ids = _pending_ids(s, args.limit)
    out = ExtractBatchResult()
    started = time.monotonic()
    for n, raw_id in enumerate(ids, 1):
        t0 = time.monotonic()
        r = extract_posting(ExtractPostingInput(raw_id=raw_id))
        out.processed += 1
        eta_min = (time.monotonic() - started) / n * (len(ids) - n) / 60
        log.info("extract %d/%d %-9s %4.1fs  %s  (about %.0f min left)",
                 n, len(ids), r.outcome, time.monotonic() - t0, (r.title or r.error or "")[:60], eta_min)
        setattr(out, r.outcome, getattr(out, r.outcome) + 1)
        if r.outcome == "failed":
            out.failed_raw_ids.append(raw_id)
    out.failed_raw_ids = out.failed_raw_ids[:MAX_IDS_RETURNED]
    with session() as s:
        out.remaining = len(_pending_ids(s, None))
    return out


class RetryFailedInput(BaseModel):
    limit: int | None = Field(None, ge=1, description="Maximum failed jobs to reset; omit for all")


class RetryFailedResult(BaseModel):
    reset: int


@tool_spec(
    name="reset_failed_extractions",
    description=(
        "Remove jobs whose extraction failed so their raw postings become pending again. "
        "Call extract_pending_postings afterwards to retry them."
    ),
)
def reset_failed_extractions(args: RetryFailedInput) -> RetryFailedResult:
    with session() as s, s.begin():
        ids = list(s.scalars(select(Job.id).where(Job.status == "extract_failed").limit(args.limit)))
        if ids:
            s.execute(delete(Job).where(Job.id.in_(ids)))  # job_sources rows cascade
    return RetryFailedResult(reset=len(ids))
