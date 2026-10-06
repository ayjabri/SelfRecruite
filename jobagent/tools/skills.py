"""Tools: technical-skill gap for a job (rule-based, no LLM)."""
from pydantic import BaseModel, Field
from sqlalchemy import select

from jobagent.db import session
from jobagent.models import Job
from jobagent.skills import my_skills, skill_gap
from jobagent.tools.registry import tool_spec


class JobSkillsInput(BaseModel):
    user_id: int | None = Field(None, description="Whose skills to compare with; default: the current user")
    job_id: int = Field(description="jobs.id")


class JobSkillsResult(BaseModel):
    job_id: int
    matching: list[str] = []
    missing: list[str] = []
    error: str | None = None


@tool_spec(
    name="job_skills",
    description="Technical skills a job posting asks for, split into those on the resume and those missing.",
)
def job_skills(args: JobSkillsInput) -> JobSkillsResult:
    with session() as s:
        text = s.scalar(select(Job.description_text).where(Job.id == args.job_id))
        if text is None:
            return JobSkillsResult(job_id=args.job_id, error="no such job")
    return JobSkillsResult(job_id=args.job_id, **skill_gap(text, args.user_id))


class MySkillsInput(BaseModel):
    user_id: int | None = Field(None, description="Default: the current user")


class MySkillsResult(BaseModel):
    skills: list[str]


@tool_spec(name="my_skills", description="The technical skills the user's resume and profile say they have.")
def list_my_skills(args: MySkillsInput) -> MySkillsResult:
    return MySkillsResult(skills=sorted(my_skills(args.user_id)))
