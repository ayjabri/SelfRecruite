"""Tools behind the keywords agent and the job conversation.

- job_keywords_context: what to read before extracting (no LLM).
- save_job_keywords: validated storage of the agent's result.
- job_chat: one user message through the job graph (orchestrator -> specialist -> stored turn).
"""
import logging
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from jobagent.db import session
from jobagent.models import Company, Job, JobKeywords, JobMessage
from jobagent.tools.registry import tool_spec
from jobagent.users import resolve_user

log = logging.getLogger(__name__)
Category = Literal["skill", "tool", "domain", "certification", "soft", "title"]


class KeywordItem(BaseModel):
    term: str = Field(min_length=1, max_length=80)
    category: Category
    weight: int = Field(ge=1, le=3)
    evidence: str | None = Field(None, max_length=200)

    @field_validator("term")
    @classmethod
    def _strip(cls, v: str) -> str:
        return " ".join(v.split())


# --- context --------------------------------------------------------------------------------

class KeywordsContextInput(BaseModel):
    user_id: int | None = Field(None, description="Whose profile defines 'similar'; default: the current user")
    job_id: int = Field(description="jobs.id")
    n_similar: int = Field(25, ge=5, le=60, description="How many similar postings to draw terms from")


class KeywordsContextResult(BaseModel):
    job_id: int
    title: str | None = None
    company: str | None = None
    posting_text: str = ""
    similar_roles: str = ""                 # the SIMILAR ROLES block, ready for the prompt
    similar_jobs: list[dict] = []
    terms: list[dict] = []
    error: str | None = None


@tool_spec(
    name="job_keywords_context",
    description=(
        "Everything to read before extracting a job's keywords: the full posting text and the terms "
        "that similar postings ask for (from the stored job embeddings and the skill catalogue; no LLM)."
    ),
)
def job_keywords_context(args: KeywordsContextInput) -> KeywordsContextResult:
    from jobagent.keywords.context import context_block, similar_terms
    uid = resolve_user(args.user_id)
    with session() as s:
        row = s.execute(select(Job.title, Company.name, Job.description_text).join(Company, Company.id == Job.company_id)
                        .where(Job.id == args.job_id)).first()
    if row is None:
        return KeywordsContextResult(job_id=args.job_id, error="no such job")
    ctx = similar_terms(args.job_id, uid, args.n_similar)
    return KeywordsContextResult(job_id=args.job_id, title=row.title, company=row.name, posting_text=row.description_text or "",
                                 similar_roles=context_block(ctx), similar_jobs=ctx["similar"], terms=ctx["terms"])


# --- result ---------------------------------------------------------------------------------

class SaveKeywordsInput(BaseModel):
    user_id: int | None = Field(None, description="Whose result; default: the current user")
    job_id: int = Field(description="jobs.id")
    keywords: list[KeywordItem] = Field(min_length=1, max_length=60, description="The extracted keywords")
    summary: str | None = Field(None, max_length=400, description="One sentence on what matters most")


class SaveKeywordsResult(BaseModel):
    job_id: int
    saved: int = 0
    error: str | None = None


@tool_spec(
    name="save_job_keywords",
    description="Store the extracted keywords for the user on this job (replaces an earlier result). Validates the shape.",
)
def save_job_keywords(args: SaveKeywordsInput) -> SaveKeywordsResult:
    from jobagent.llm import model_spec
    from jobagent.prompts import get_prompt
    uid = resolve_user(args.user_id)
    seen, items = set(), []
    for k in args.keywords:
        key = k.term.lower()
        if key not in seen:
            seen.add(key)
            items.append(k.model_dump())
    with session() as s, s.begin():
        if s.get(Job, args.job_id) is None:
            return SaveKeywordsResult(job_id=args.job_id, error="no such job")
        row = s.get(JobKeywords, (uid, args.job_id)) or JobKeywords(user_id=uid, job_id=args.job_id, keywords=[])
        row.keywords, row.summary = items, args.summary
        row.model, row.prompt_version = model_spec(uid), get_prompt("keywords")[1]
        row.created_at = datetime.now(timezone.utc)
        s.add(row)
    return SaveKeywordsResult(job_id=args.job_id, saved=len(items))


# --- the conversation -----------------------------------------------------------------------

class JobChatInput(BaseModel):
    user_id: int | None = Field(None, description="Who is asking; default: the current user")
    job_id: int = Field(description="jobs.id the conversation is about")
    message: str = Field(min_length=1, max_length=4000, description="What the user said, e.g. 'extract keywords'")


class JobChatResult(BaseModel):
    job_id: int
    agent: str | None = None         # which specialist answered, or null for the orchestrator
    reply: str = ""
    tool_calls: list[dict] = []
    model: str | None = None
    error: str | None = None


@tool_spec(
    name="job_chat",
    description=(
        "Send one message to the AI about a job. An orchestrator routes it to the right specialist "
        "(e.g. 'extract keywords' -> the keywords agent); the reply and any stored result come back. "
        "Uses the user's chosen model: the shared local one (daily budget) or their own Claude account."
    ),
)
def job_chat(args: JobChatInput) -> JobChatResult:
    from jobagent.agents.job.graph import chat
    uid = resolve_user(args.user_id)
    with session() as s:
        if s.get(Job, args.job_id) is None:
            return JobChatResult(job_id=args.job_id, error="no such job")
    turn = chat(uid, args.job_id, args.message)
    return JobChatResult(job_id=args.job_id, agent=turn.get("agent"), reply=turn.get("content", ""),
                         tool_calls=turn.get("tool_calls") or [], model=turn.get("model"), error=turn.get("error"))


def transcript(user_id: int, job_id: int, limit: int = 50) -> list[dict]:
    with session() as s:
        rows = s.scalars(select(JobMessage).where(JobMessage.user_id == user_id, JobMessage.job_id == job_id)
                         .order_by(JobMessage.id.desc()).limit(limit)).all()
    return [{"id": m.id, "role": m.role, "content": m.content, "agent": m.agent, "model": m.model, "error": m.error,
             "created_at": m.created_at.isoformat(timespec="seconds")} for m in reversed(rows)]
