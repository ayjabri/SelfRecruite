"""Embedding tools: vectors for jobs and for the golden-source resume (Ollama, no chat LLM)."""
import hashlib
import logging
import re

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from jobagent.config import get_settings
from jobagent.db import session
from jobagent.models import GoldenChunk, Job
from jobagent.parse.schema import ParsedJob
from jobagent.tools.registry import tool_spec

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


# --- golden source ------------------------------------------------------------------------

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
    return chunks


class IndexGoldenInput(BaseModel):
    force: bool = Field(False, description="Rebuild the index even if the file has not changed")


class IndexGoldenResult(BaseModel):
    source_sha: str | None = None
    chunks: int = 0
    always_include: int = 0
    rebuilt: bool = False
    error: str | None = None


@tool_spec(
    name="index_golden_source",
    description=(
        "Split the golden-source resume by heading, embed each section and store it for retrieval. "
        "Does nothing if the file is unchanged since the last index."
    ),
)
def index_golden_source(args: IndexGoldenInput) -> IndexGoldenResult:
    path = get_settings().golden_source_file
    if not path.exists():
        return IndexGoldenResult(error=f"golden source file not found: {path}")
    text = path.read_text()
    sha = hashlib.sha256(text.encode()).hexdigest()
    with session() as s:
        existing = s.scalar(select(func.count()).select_from(GoldenChunk).where(GoldenChunk.source_sha == sha))
        if existing and not args.force:
            pinned = s.scalar(select(func.count()).select_from(GoldenChunk).where(
                GoldenChunk.source_sha == sha, GoldenChunk.always_include))
            return IndexGoldenResult(source_sha=sha, chunks=existing, always_include=pinned)
    chunks = chunk_markdown(text)
    try:
        vectors = embed_texts([f"{h}\n{b}" for h, b in chunks], DOC)
    except httpx.HTTPError as e:
        return IndexGoldenResult(source_sha=sha, error=f"embedding failed: {type(e).__name__}: {e}")
    rows = [
        GoldenChunk(source_sha=sha, position=i, heading=h, text=b, embedding=v,
                    always_include=bool(ALWAYS_INCLUDE.search(h)))
        for i, ((h, b), v) in enumerate(zip(chunks, vectors))
    ]
    result = IndexGoldenResult(source_sha=sha, chunks=len(rows), rebuilt=True,
                               always_include=sum(r.always_include for r in rows))
    with session() as s, s.begin():
        s.execute(delete(GoldenChunk))  # one resume version at a time
        s.add_all(rows)
    log.info("golden source indexed: %d sections", result.chunks)
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
