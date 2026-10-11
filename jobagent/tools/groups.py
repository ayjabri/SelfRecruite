"""What a user's company groups and interest statements do to their jobs. No LLM."""
from pydantic import BaseModel, Field
from sqlalchemy import select

from jobagent.db import session
from jobagent.groups import Segments, validate
from jobagent.models import Company, Job, UserJob
from jobagent.tools.registry import tool_spec
from jobagent.tools.scoring import job_in_scope, posting_bullets
from jobagent.users import get_profile, resolve_user


class RulesReportInput(BaseModel):
    user_id: int | None = Field(None, description="Whose groups; default: the current user")
    draft: dict | None = Field(None, description=(
        "Groups and interest statements to try instead of the saved ones, as "
        "{company_groups: {...}, company_interest: {...}}; nothing is saved"))


class SegmentCount(BaseModel):
    name: str
    kind: str                  # "group" | "company" | "none"
    jobs: int = 0              # jobs in the user's title and location scope at these companies
    qualify: int = 0
    capped: int = 0
    hidden: int = 0
    conflicts: int = 0         # marked interested, but a statement caps or hides them


class RulesReportResult(BaseModel):
    segments: list[SegmentCount] = []
    conflict_job_ids: list[int] = []
    companies: dict[str, str | None] = {}   # every company with jobs in scope -> its group
    error: str | None = None


@tool_spec(
    name="company_rules_report",
    description=(
        "Count what the user's company groups and interest statements do: per group, and per company with "
        "its own statement, how many in-scope jobs qualify, are capped or are hidden, and how many jobs the "
        "user marked interested conflict with a statement. Pass a draft to preview a change without saving."
    ),
)
def company_rules_report(args: RulesReportInput) -> RulesReportResult:
    uid = resolve_user(args.user_id)
    profile = get_profile(uid)
    targets = profile["targets"]
    out = RulesReportResult()
    if args.draft is not None:
        try:
            targets = {**targets, **validate(args.draft, targets.get("title_families") or {})}
        except ValueError as e:
            out.error = str(e)
            return out
    segs = Segments(targets)
    with session() as s:
        rows = s.execute(
            select(Job.id, Job.title, Job.locations, Job.workplace_type, Job.parsed, Company.name, UserJob.user_label)
            .join(Company, Company.id == Job.company_id)
            .outerjoin(UserJob, (UserJob.job_id == Job.id) & (UserJob.user_id == uid))
            .where(Job.duplicate_of.is_(None), Job.parsed.is_not(None))
        ).all()
    counts: dict[tuple[str, str], SegmentCount] = {}
    for r in rows:
        if not job_in_scope(r.title, r.locations, r.workplace_type, profile["scope"])[0]:
            continue
        verdict = segs.check(r.name, r.title)
        if verdict.action and segs.needs_bullets:
            verdict = segs.check(r.name, r.title, posting_bullets(r.parsed))
        out.companies[r.name] = verdict.group
        kind = "company" if verdict.segment == r.name and verdict.rule_from == "company" else "group" if verdict.group else "none"
        name = verdict.segment if kind != "none" else "No group"
        c = counts.setdefault((kind, name), SegmentCount(name=name, kind=kind))
        c.jobs += 1
        c.qualify += verdict.action is None
        c.capped += verdict.action == "cap"
        c.hidden += verdict.action == "hide"
        if verdict.action and r.user_label == "interested":
            c.conflicts += 1
            out.conflict_job_ids.append(r.id)
    out.segments = sorted(counts.values(), key=lambda c: (c.kind == "none", c.kind != "group", c.name.lower()))
    out.companies = dict(sorted(out.companies.items()))
    return out
