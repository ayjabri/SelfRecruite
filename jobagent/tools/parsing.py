"""Parse tools: turn kept raw postings into jobs rows with rule-based parsing (no LLM)."""
import logging
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from jobagent.db import session
from jobagent.models import Company, Job, JobSource, RawPosting
from jobagent.parse.core import parse_posting as parse
from jobagent.parse.schema import ParsedJob
from jobagent.tools.common import (
    MAX_IDS_RETURNED, NORMALIZERS, NormalizedPosting, get_company, norm_company, norm_title,
)
from jobagent.tools.filtering import KEPT, FilterInput, filter_pending_postings
from jobagent.tools.registry import tool_spec

log = logging.getLogger(__name__)


def _pending_ids(s, limit: int | None) -> list[int]:
    """Postings the pre-filter kept that have no job yet."""
    linked = select(JobSource.raw_id).where(JobSource.raw_id == RawPosting.id).exists()
    return list(s.scalars(
        select(RawPosting.id).where(~linked, RawPosting.filter_status == KEPT)
        .order_by(RawPosting.id).limit(limit)
    ))


def _apply(job: Job, posting: NormalizedPosting, parsed: ParsedJob) -> None:
    """Copy the parse result onto the job's columns."""
    job.title, job.title_norm = posting.title, norm_title(posting.title)
    job.description_text, job.apply_url = posting.description_text, posting.url
    job.source_posted_at = posting.posted_at
    job.parsed = parsed.model_dump(mode="json")
    job.locations = [loc.model_dump() for loc in parsed.locations]
    job.workplace_type, job.seniority = parsed.workplace_type, parsed.seniority
    job.comp_min, job.comp_max, job.comp_period = parsed.comp_min, parsed.comp_max, parsed.comp_period


class ParsePostingInput(BaseModel):
    raw_id: int = Field(description="raw_postings.id to parse")


class ParseResult(BaseModel):
    raw_id: int
    outcome: Literal["parsed", "linked", "skipped", "failed"]
    job_id: int | None = None
    title: str | None = None
    sections_quality: str | None = None
    error: str | None = None


@tool_spec(
    name="parse_posting",
    description=(
        "Parse one stored posting into a jobs row using rules only (no LLM): locations, pay, years, "
        "workplace type and the requirement bullets. Links to an existing job when the company and "
        "requisition ID match. Does nothing if the posting already has a job."
    ),
)
def parse_posting(args: ParsePostingInput) -> ParseResult:
    with session() as s, s.begin():
        raw = s.get(RawPosting, args.raw_id)
        if raw is None:
            return ParseResult(raw_id=args.raw_id, outcome="failed", error="no such raw posting")
        if s.scalar(select(JobSource.job_id).where(JobSource.raw_id == raw.id)):
            return ParseResult(raw_id=raw.id, outcome="skipped")
        normalizer = NORMALIZERS.get(raw.source)
        if normalizer is None:
            return ParseResult(raw_id=raw.id, outcome="failed", error=f"no normalizer for source '{raw.source}'")
        try:
            posting = normalizer(raw.payload)
            parsed = parse(posting)
        except Exception as e:  # noqa: BLE001 - one odd posting must not block the batch
            return ParseResult(raw_id=raw.id, outcome="failed", error=f"{type(e).__name__}: {e}"[:300])
        if posting.requisition_id:  # same company + requisition = same opening
            existing = s.scalar(
                select(Job.id).join(Company, Company.id == Job.company_id).where(
                    Company.name_norm == norm_company(posting.company),
                    Job.requisition_id == posting.requisition_id)
            )
            if existing:
                s.add(JobSource(job_id=existing, raw_id=raw.id))
                return ParseResult(raw_id=raw.id, outcome="linked", job_id=existing, title=posting.title)
        company = get_company(s, posting.company, raw.source, raw.target)
        job = Job(company_id=company.id, requisition_id=posting.requisition_id, status="parsed")
        _apply(job, posting, parsed)
        s.add(job)
        s.flush()
        s.add(JobSource(job_id=job.id, raw_id=raw.id))
        return ParseResult(raw_id=raw.id, outcome="parsed", job_id=job.id, title=posting.title,
                           sections_quality=parsed.sections_quality)


class ParsePendingInput(BaseModel):
    limit: int | None = Field(None, ge=1, description="Maximum postings to parse in this call; omit for all")
    reparse: bool = Field(False, description=(
        "Also re-run the parser on jobs that already exist, e.g. after the parsing rules changed. "
        "LLM enrichment and scores on those jobs are left untouched."))


class ParseBatchResult(BaseModel):
    processed: int = 0
    parsed: int = 0
    linked: int = 0
    skipped: int = 0
    failed: int = 0
    reparsed: int = 0
    sections_labelled: int = 0     # requirement and preferred bullets told apart by headings
    sections_unlabelled: int = 0   # bullets found, headings not recognised
    sections_none: int = 0         # no bullets in the posting
    remaining: int = 0
    failed_raw_ids: list[int] = []


def _reparse_existing(out: ParseBatchResult) -> None:
    with session() as s, s.begin():
        first_raw = (
            select(JobSource.job_id, func.min(JobSource.raw_id).label("raw_id"))
            .group_by(JobSource.job_id).subquery()
        )
        rows = s.execute(
            select(Job, RawPosting).join(first_raw, first_raw.c.job_id == Job.id)
            .join(RawPosting, RawPosting.id == first_raw.c.raw_id)
        ).all()
        for job, raw in rows:
            normalizer = NORMALIZERS.get(raw.source)
            if normalizer is None:
                continue
            try:
                posting = normalizer(raw.payload)
                parsed = parse(posting)
            except Exception:  # noqa: BLE001
                out.failed += 1
                continue
            _apply(job, posting, parsed)
            out.reparsed += 1
            setattr(out, f"sections_{parsed.sections_quality}", getattr(out, f"sections_{parsed.sections_quality}") + 1)


@tool_spec(
    name="parse_pending_postings",
    description=(
        "Parse every stored posting that passed the pre-filter and has no job yet, using rules only "
        "(no LLM, takes seconds). Unchecked postings are filtered first. Returns counts per outcome "
        "and how many postings had recognisable requirement sections."
    ),
)
def parse_pending_postings(args: ParsePendingInput) -> ParseBatchResult:
    filter_pending_postings(FilterInput())
    out = ParseBatchResult()
    if args.reparse:
        _reparse_existing(out)
    with session() as s:
        ids = _pending_ids(s, args.limit)
    for raw_id in ids:
        r = parse_posting(ParsePostingInput(raw_id=raw_id))
        out.processed += 1
        setattr(out, r.outcome, getattr(out, r.outcome) + 1)
        if r.sections_quality:
            key = f"sections_{r.sections_quality}"
            setattr(out, key, getattr(out, key) + 1)
        if r.outcome == "failed":
            out.failed_raw_ids.append(raw_id)
            log.info("parse failed for raw %d: %s", raw_id, r.error)
    out.failed_raw_ids = out.failed_raw_ids[:MAX_IDS_RETURNED]
    with session() as s:
        out.remaining = len(_pending_ids(s, None))
    log.info("parsed %d postings (%d linked, %d failed)", out.parsed, out.linked, out.failed)
    return out
