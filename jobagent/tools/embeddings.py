"""Embedding tools: vectors for jobs and for each user's resume (Ollama, no chat LLM)."""
import hashlib
import logging
import re
from pathlib import Path

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

from jobagent.config import get_settings
from jobagent.db import session
from jobagent.models import GoldenChunk, Job, Resume
from jobagent.parse.schema import ParsedJob
from jobagent.tools.registry import tool_spec
from jobagent.users import DEFAULT_USER_ID, active_resume, resolve_user

log = logging.getLogger(__name__)

BATCH = 32
MAX_CHARS = 6000  # keeps every input inside the embedding model's context
# nomic-embed-text is trained with task prefixes; documents and queries must use the matching one
DOC, QUERY = "search_document: ", "search_query: "
ALWAYS_INCLUDE = re.compile(r"open items|future tailoring instructions", re.I)


def embed_texts(texts: list[str], prefix: str = DOC) -> list[list[float]]:
    """Embed texts in batches. Raises httpx.HTTPError if Ollama is unreachable."""
    s = get_settings()
    out: list[list[float]] = []
    with httpx.Client(timeout=120) as client:
        for i in range(0, len(texts), BATCH):
            r = client.post(f"{s.ollama_base_url}/api/embed", json={
                "model": s.embed_model, "input": [prefix + t[:MAX_CHARS] for t in texts[i:i + BATCH]],
            })
            r.raise_for_status()
            out.extend(r.json()["embeddings"])
    return out


def job_embedding_text(title: str, parsed: dict | None, description_text: str | None) -> str:
    """Title plus requirement bullets; falls back to the start of the description."""
    bullets = ParsedJob.model_validate(parsed).requirements if parsed else []
    body = "\n".join(f"- {b}" for b in bullets) if bullets else (description_text or "")[:1500]
    return f"{title}\n{body}"


# --- resume (golden source) ----------------------------------------------------------------

def chunk_markdown(text: str) -> list[tuple[str, str]]:
    """Split by heading into (heading path, body). Sections with no body text are skipped."""
    chunks: list[tuple[str, str]] = []
    path: list[tuple[int, str]] = []
    body: list[str] = []

    def flush():
        content = "\n".join(body).strip()
        if content and path:
            chunks.append((" > ".join(h for _, h in path), content))
        body.clear()

    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
        if not m:
            body.append(line)
            continue
        flush()
        level, heading = len(m.group(1)), m.group(2).strip("`* ")
        while path and path[-1][0] >= level:
            path.pop()
        path.append((level, heading))
    flush()
    if not chunks and text.strip():  # no headings at all: paragraphs become the sections
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        chunks = [(f"Section {i + 1}", p) for i, p in enumerate(paras)]
    return chunks


def sha_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class UploadResumeInput(BaseModel):
    user_id: int | None = Field(None, description="Whose resume; default: the current user")
    text: str | None = Field(None, description="The resume as markdown")
    path: str | None = Field(None, description="Or a markdown file to read instead of `text`")
    filename: str | None = Field(None, description="A name to remember the upload by")


class UploadResumeResult(BaseModel):
    resume_id: int | None = None
    sha: str | None = None
    unchanged: bool = False
    chunks: int = 0
    error: str | None = None


@tool_spec(
    name="upload_resume",
    description=(
        "Store a user's resume (markdown) as their active resume and index it for scoring. "
        "If the text is identical to the active resume nothing changes."
    ),
)
def upload_resume(args: UploadResumeInput) -> UploadResumeResult:
    uid = resolve_user(args.user_id)
    if args.text is None and args.path is None:
        return UploadResumeResult(error="give text or path")
    text = args.text if args.text is not None else Path(args.path).read_text()
    if not text.strip():
        return UploadResumeResult(error="the resume is empty")
    sha = sha_of(text)
    with session() as s, s.begin():
        current = active_resume(s, uid)
        if current and current.sha == sha:
            rid = current.id
            unchanged = True
        else:
            s.execute(update(Resume).where(Resume.user_id == uid, Resume.active).values(active=False))
            row = Resume(user_id=uid, filename=args.filename or (Path(args.path).name if args.path else None),
                         text=text, sha=sha)
            s.add(row)
            s.flush()
            rid, unchanged = row.id, False
    indexed = index_golden_source(IndexGoldenInput(user_id=uid))
    return UploadResumeResult(resume_id=rid, sha=sha, unchanged=unchanged, chunks=indexed.chunks, error=indexed.error)


class IndexGoldenInput(BaseModel):
    user_id: int | None = Field(None, description="Whose resume to index; default: the current user")
    force: bool = Field(False, description="Rebuild the index even if the resume has not changed")


class IndexGoldenResult(BaseModel):
    source_sha: str | None = None
    chunks: int = 0
    always_include: int = 0
    rebuilt: bool = False
    error: str | None = None


def _import_file_resume(uid: int) -> Resume | None:
    """First run for the default user: the GOLDEN_SOURCE_FILE becomes their stored resume."""
    path = get_settings().golden_source_file
    if uid != DEFAULT_USER_ID or not path.exists():
        return None
    text = path.read_text()
    with session() as s, s.begin():
        row = Resume(user_id=uid, filename=path.name, text=text, sha=sha_of(text))
        s.add(row)
        s.flush()
        s.expunge(row)
    return row


@tool_spec(
    name="index_golden_source",
    description=(
        "Split the user's active resume by heading, embed each section and store it for retrieval. "
        "Does nothing if the resume is unchanged since the last index."
    ),
)
def index_golden_source(args: IndexGoldenInput) -> IndexGoldenResult:
    uid = resolve_user(args.user_id)
    with session() as s:
        resume = active_resume(s, uid)
        if resume is not None:
            s.expunge(resume)
    if resume is None:
        resume = _import_file_resume(uid)
    if resume is None:
        return IndexGoldenResult(error="no resume stored for this user; run upload_resume first")
    sha = resume.sha
    with session() as s:
        existing = s.scalar(select(func.count()).select_from(GoldenChunk).where(
            GoldenChunk.user_id == uid, GoldenChunk.source_sha == sha))
        if existing and not args.force:
            pinned = s.scalar(select(func.count()).select_from(GoldenChunk).where(
                GoldenChunk.user_id == uid, GoldenChunk.source_sha == sha, GoldenChunk.always_include))
            return IndexGoldenResult(source_sha=sha, chunks=existing, always_include=pinned)
    chunks = chunk_markdown(resume.text)
    try:
        vectors = embed_texts([f"{h}\n{b}" for h, b in chunks], DOC)
    except httpx.HTTPError as e:
        return IndexGoldenResult(source_sha=sha, error=f"embedding failed: {type(e).__name__}: {e}")
    rows = [
        GoldenChunk(user_id=uid, resume_id=resume.id, source_sha=sha, position=i, heading=h, text=b,
                    embedding=v, always_include=bool(ALWAYS_INCLUDE.search(h)))
        for i, ((h, b), v) in enumerate(zip(chunks, vectors))
    ]
    result = IndexGoldenResult(source_sha=sha, chunks=len(rows), rebuilt=True,
                               always_include=sum(r.always_include for r in rows))
    with session() as s, s.begin():
        s.execute(delete(GoldenChunk).where(GoldenChunk.user_id == uid))  # one resume version per user
        s.add_all(rows)
    log.info("resume indexed for user %d: %d sections", uid, result.chunks)
    return result


# --- jobs ---------------------------------------------------------------------------------

class EmbedJobsInput(BaseModel):
    limit: int | None = Field(None, ge=1, description="Maximum jobs to embed in this call; omit for all")
    reembed: bool = Field(False, description="Recompute embeddings for jobs that already have one")


class EmbedJobsResult(BaseModel):
    embedded: int = 0
    remaining: int = 0
    error: str | None = None


@tool_spec(
    name="embed_jobs",
    description=(
        "Compute the embedding (title plus requirement bullets) for jobs that do not have one yet. "
        "Used by de-duplication and the fast score. Takes a few seconds per hundred jobs."
    ),
)
def embed_jobs(args: EmbedJobsInput) -> EmbedJobsResult:
    out = EmbedJobsResult()
    with session() as s:
        query = select(Job.id, Job.title, Job.parsed, Job.description_text).order_by(Job.id).limit(args.limit)
        if not args.reembed:
            query = query.where(Job.embedding.is_(None))
        rows = s.execute(query).all()
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        try:
            vectors = embed_texts([job_embedding_text(t, p, d) for _, t, p, d in batch], DOC)
        except httpx.HTTPError as e:
            out.error = f"embedding failed: {type(e).__name__}: {e}"
            break
        with session() as s, s.begin():
            for (job_id, *_), v in zip(batch, vectors):
                s.get(Job, job_id).embedding = v
        out.embedded += len(batch)
    with session() as s:
        out.remaining = s.scalar(select(func.count()).select_from(Job).where(Job.embedding.is_(None)))
    log.info("embedded %d jobs, %d remaining", out.embedded, out.remaining)
    return out
