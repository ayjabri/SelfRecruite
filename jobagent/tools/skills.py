"""Tools: technical-skill gap for a job (rule-based, no LLM)."""
from pydantic import BaseModel, Field
from sqlalchemy import select

from jobagent.db import session
from jobagent.models import Job
from jobagent.skills import my_skills, skill_gap
from jobagent.tools.registry import tool_spec


class JobSkillsInput(BaseModel):
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
    return JobSkillsResult(job_id=args.job_id, **skill_gap(text))


class MySkillsInput(BaseModel):
    pass


class MySkillsResult(BaseModel):
    skills: list[str]


@tool_spec(name="my_skills", description="The technical skills the resume and config/skills.yaml say you have.")
def list_my_skills(args: MySkillsInput) -> MySkillsResult:
    return MySkillsResult(skills=sorted(my_skills()))
