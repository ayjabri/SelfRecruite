from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger, Boolean, Date, ForeignKey, Index, Integer, LargeBinary, Numeric, SmallInteger, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)        # stored lowercase
    name: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)      # null until the user sets one
    role: Mapped[str] = mapped_column(Text, server_default="member")   # admin | member
    status: Mapped[str] = mapped_column(Text, server_default="active")  # active | disabled
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())


class Profile(Base):
    """What one user is looking for. The shapes mirror the config/*.yaml templates."""
    __tablename__ = "profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    targets: Mapped[dict] = mapped_column(JSONB, server_default="{}")   # weights, title_families, seniority, domains, gates
    scope: Mapped[dict] = mapped_column(JSONB, server_default="{}")     # title include/exclude, location states
    skills: Mapped[dict] = mapped_column(JSONB, server_default="{}")    # have, not_have
    llm: Mapped[dict] = mapped_column(JSONB, server_default="{}")       # provider, model, encrypted key, judge budget
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())


class Resume(Base):
    """A user's resume text (markdown). One is active at a time."""
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    sha: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    uploaded_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())


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
    # parsed -> deduped | duplicate | failed  (per-user states live in user_jobs)
    status: Mapped[str] = mapped_column(Text, server_default="parsed")
    # set when this row was merged into another job (the canonical record); such rows are hidden
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), index=True)


class UserJob(Base):
    """Everything one user knows or decided about one job: scope, scores, marks, notes, judgement."""
    __tablename__ = "user_jobs"
    __table_args__ = (
        Index("user_jobs_score_idx", "user_id", "fit_score"),
        Index("user_jobs_label_idx", "user_id", "user_label"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    job_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    in_scope: Mapped[bool] = mapped_column(Boolean, server_default="true")   # matches the profile's scope
    fit_score: Mapped[int | None] = mapped_column(SmallInteger)              # fast score, no LLM, 0-100
    fit_details: Mapped[dict | None] = mapped_column(JSONB)                  # components, per-bullet matches, gates
    user_label: Mapped[str | None] = mapped_column(Text)                     # interested | not_interested
    user_labeled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    favorite: Mapped[bool] = mapped_column(Boolean, server_default="false")
    applied_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    match_score: Mapped[int | None] = mapped_column(SmallInteger)            # LLM judgement, 0-100
    match_explanation: Mapped[str | None] = mapped_column(Text)
    match_details: Mapped[dict | None] = mapped_column(JSONB)
    match_model: Mapped[str | None] = mapped_column(Text)
    golden_source_sha: Mapped[str | None] = mapped_column(Text)              # resume version judged against
    scored_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())


class PreferenceModelRow(Base):
    """One trained preference model per user (joblib bytes)."""
    __tablename__ = "preference_models"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    trained_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    meta: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    blob: Mapped[bytes] = mapped_column(LargeBinary)


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
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))  # null = system crawl
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
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"))
    source_sha: Mapped[str] = mapped_column(Text, index=True)  # sha256 of the resume text this came from
    position: Mapped[int] = mapped_column(Integer)             # order in the file
    heading: Mapped[str] = mapped_column(Text)                 # "Citi > Flagship Project > What I Built"
    text: Mapped[str] = mapped_column(Text)
    # True for sections handed to scoring and tailoring in full instead of being retrieved
    always_include: Mapped[bool] = mapped_column(Boolean, server_default="false")
    embedding = mapped_column(Vector(768))


class PossibleDuplicate(Base):
    """A same-company pair that looked alike but not enough to merge automatically."""
    __tablename__ = "possible_duplicates"
    __table_args__ = (UniqueConstraint("job_a", "job_b"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_a: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    job_b: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    similarity: Mapped[float] = mapped_column(Numeric(5, 3))
    title_ratio: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(Text, server_default="pending")  # pending | same | different
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
