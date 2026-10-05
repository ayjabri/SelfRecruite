from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger, Boolean, Date, ForeignKey, Index, Integer, Numeric, SmallInteger, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    name_norm: Mapped[str] = mapped_column(Text, unique=True)  # lowercased, Inc/LLC removed
    ats: Mapped[str | None] = mapped_column(Text)
    ats_slug: Mapped[str | None] = mapped_column(Text)
    careers_url: Mapped[str | None] = mapped_column(Text)


class RawPosting(Base):
    """Exactly what each source returned."""
    __tablename__ = "raw_postings"
    __table_args__ = (UniqueConstraint("source", "external_id", "content_hash"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(Text)  # workday | oracle | greenhouse | jobspy_indeed ...
    target: Mapped[str | None] = mapped_column(Text)  # what was queried: board slug, tenant/site, keyword
    external_id: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(Text)  # sha256 of normalized description text
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    # pre-filter decision: NULL = not checked yet, 'kept' = extract it, 'filtered' = skip it
    filter_status: Mapped[str | None] = mapped_column(Text, index=True)
    filter_reason: Mapped[str | None] = mapped_column(Text)


class Job(Base):
    """One row per real-world opening."""
    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            "jobs_req_uq", "company_id", "requisition_id", unique=True,
            postgresql_where=text("requisition_id IS NOT NULL"),
        ),
        Index("jobs_status_idx", "status"),
        Index(
            "jobs_embed_idx", "embedding", postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    requisition_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    title_norm: Mapped[str] = mapped_column(Text)
    locations: Mapped[list | None] = mapped_column(JSONB)
    workplace_type: Mapped[str | None] = mapped_column(Text)
    seniority: Mapped[str | None] = mapped_column(Text)
    description_text: Mapped[str | None] = mapped_column(Text)
    parsed: Mapped[dict | None] = mapped_column(JSONB)     # ParsedJob: what code read, no LLM
    extracted: Mapped[dict | None] = mapped_column(JSONB)  # JobExtract: LLM enrichment, on demand
    comp_min: Mapped[Decimal | None] = mapped_column(Numeric)
    comp_max: Mapped[Decimal | None] = mapped_column(Numeric)
    comp_period: Mapped[str | None] = mapped_column(Text)
    apply_url: Mapped[str | None] = mapped_column(Text)
    embedding = mapped_column(Vector(768))  # title + requirements, for dedup and prefilter
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    source_posted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # parsed -> deduped -> prescored -> scored -> queued_tailor -> tailored | skipped | failed
    status: Mapped[str] = mapped_column(Text, server_default="parsed")
    match_score: Mapped[int | None] = mapped_column(SmallInteger)  # 0-100
    match_explanation: Mapped[str | None] = mapped_column(Text)
    match_details: Mapped[dict | None] = mapped_column(JSONB)
    match_model: Mapped[str | None] = mapped_column(Text)
    golden_source_sha: Mapped[str | None] = mapped_column(Text)
    scored_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    fit_score: Mapped[int | None] = mapped_column(SmallInteger, index=True)  # fast score, no LLM, 0-100
    fit_details: Mapped[dict | None] = mapped_column(JSONB)       # components, per-bullet matches, gates
    user_label: Mapped[str | None] = mapped_column(Text, index=True)  # interested | not_interested
    user_labeled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    resume_path: Mapped[str | None] = mapped_column(Text)
    tailored_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class JobSource(Base):
    """Every posting that points to a job."""
    __tablename__ = "job_sources"

    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    raw_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("raw_postings.id"), primary_key=True
    )


class Run(Base):
    """One row per stage per day."""
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_date: Mapped[date | None] = mapped_column(Date)
    stage: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    status: Mapped[str | None] = mapped_column(Text)
    counts: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric)


class GoldenChunk(Base):
    """One heading-level section of the golden-source resume, embedded for retrieval."""
    __tablename__ = "golden_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_sha: Mapped[str] = mapped_column(Text, index=True)  # sha256 of the file this came from
    position: Mapped[int] = mapped_column(Integer)             # order in the file
    heading: Mapped[str] = mapped_column(Text)                 # "Citi > Flagship Project > What I Built"
    text: Mapped[str] = mapped_column(Text)
    # True for sections handed to scoring and tailoring in full instead of being retrieved
    always_include: Mapped[bool] = mapped_column(Boolean, server_default="false")
    embedding = mapped_column(Vector(768))
