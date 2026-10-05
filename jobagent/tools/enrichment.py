"""Enrichment tools: on-demand LLM extraction for jobs that are worth the time."""
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from jobagent.db import session
from jobagent.extract.core import llm_extract
from jobagent.models import Job, JobSource, RawPosting
from jobagent.tools.common import NORMALIZERS
from jobagent.tools.registry import tool_spec


class EnrichJobInput(BaseModel):
    job_id: int = Field(description="jobs.id to enrich")
    force: bool = Field(False, description="Run the LLM again even if the job is already enriched")


class EnrichResult(BaseModel):
    job_id: int
    outcome: str  # enriched | already_enriched | failed
    title: str | None = None
    error: str | None = None


@tool_spec(
    name="enrich_job",
    description=(
        "Run the LLM on one job to extract what rules cannot: a summary, responsibilities, required "
        "and preferred qualifications, and skills. Slow (about 10 seconds); meant for jobs that have "
        "already ranked well. The result is stored on the job and reused."
    ),
)
def enrich_job(args: EnrichJobInput) -> EnrichResult:
    with session() as s:  # read, then release the connection before the slow LLM call
        job = s.get(Job, args.job_id)
        if job is None:
            return EnrichResult(job_id=args.job_id, outcome="failed", error="no such job")
        if job.extracted and not args.force:
            return EnrichResult(job_id=job.id, outcome="already_enriched", title=job.title)
        raw = s.scalar(
            select(RawPosting).join(JobSource, JobSource.raw_id == RawPosting.id)
            .where(JobSource.job_id == job.id).order_by(RawPosting.id).limit(1)
        )
        if raw is None or raw.source not in NORMALIZERS:
            return EnrichResult(job_id=job.id, outcome="failed", title=job.title, error="no source posting")
        posting = NORMALIZERS[raw.source](raw.payload)
    try:
        extracted = llm_extract(posting).model_dump()
    except Exception as e:  # noqa: BLE001
        return EnrichResult(job_id=args.job_id, outcome="failed", title=posting.title,
                            error=f"{type(e).__name__}: {e}"[:300])
    with session() as s, s.begin():
        s.get(Job, args.job_id).extracted = extracted
    return EnrichResult(job_id=args.job_id, outcome="enriched", title=posting.title)


class EnrichmentStatusInput(BaseModel):
    pass


class EnrichmentStatus(BaseModel):
    jobs: int
    enriched: int


@tool_spec(
    name="enrichment_status",
    description="Count the jobs in the database and how many of them have LLM enrichment.",
)
def enrichment_status(args: EnrichmentStatusInput) -> EnrichmentStatus:
    with session() as s:
        total = s.scalar(select(func.count()).select_from(Job))
        done = s.scalar(select(func.count()).select_from(Job).where(Job.extracted.is_not(None)))
    return EnrichmentStatus(jobs=total, enriched=done)
