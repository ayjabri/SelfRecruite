"""Judgement tools: the LLM's per-requirement verdicts and the match score built from them.

Per user: the resume excerpts, the targets and the stored verdicts (user_jobs)."""
import logging
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from datetime import time as _time

from sqlalchemy import func, select

from jobagent.llm import get_chat_model, model_spec, user_llm
from jobagent.db import session
from jobagent.judge.core import judge
from jobagent.models import Company, GoldenChunk, Job, UserJob
from jobagent.parse.schema import ParsedJob
from jobagent.tools.enrichment import EnrichJobInput, enrich_job
from jobagent.tools.registry import tool_spec
from jobagent.tools.scoring import combine, in_scope
from jobagent.users import get_profile, resolve_user, user_job

log = logging.getLogger(__name__)
MAX_REQUIREMENTS = 16


def _requirements(job: Job) -> list[tuple[str, str]]:
    """Required and preferred bullets from the parser; the LLM extraction when headings were not found."""
    parsed = ParsedJob.model_validate(job.parsed) if job.parsed else None
    if parsed and parsed.sections_quality == "labelled":
        reqs = [("required", b) for b in parsed.bullets("required")] + [("preferred", b) for b in parsed.bullets("preferred")]
    elif job.extracted:
        reqs = [("required", b) for b in job.extracted.get("required_qualifications", [])] + \
               [("preferred", b) for b in job.extracted.get("preferred_qualifications", [])]
    elif parsed:
        reqs = [("required", b) for b in parsed.requirements]
    else:
        reqs = []
    return reqs[:MAX_REQUIREMENTS]


def _explanation(score: int, items: list[dict], seniority: str, sen_fit: float, gates: list[str]) -> str:
    req = [i for i in items if i["label"] == "required"]
    strong = [i for i in req if i["pct"] >= 70]
    gaps = [i for i in items if i["cls"] == "Gap" or i["pct"] < 40]
    short = lambda t: t if len(t) <= 90 else t[:90].rsplit(" ", 1)[0] + "…"  # noqa: E731
    parts = [f"{score}/100."]
    if req:
        parts.append(f"Strong on {len(strong)} of {len(req)} must-haves" + (
            f" ({'; '.join(short(i['text']) for i in strong[:3])})." if strong else "."))
    parts.append("Gaps: " + ("; ".join(short(i["missing"] or i["text"]) for i in gaps[:3]) + "." if gaps else "none found."))
    parts.append(f"Seniority: {seniority}, {'a good fit' if sen_fit >= 0.7 else 'a stretch' if sen_fit >= 0.4 else 'not a fit'}.")
    if gates:
        parts.append("Capped: " + "; ".join(gates) + ".")
    return " ".join(parts)


class JudgeJobInput(BaseModel):
    user_id: int | None = Field(None, description="Whose resume to judge against; default: the current user")
    job_id: int = Field(description="jobs.id to judge")
    force: bool = Field(False, description="Judge again even if the job already has a match score")


class JudgeResult(BaseModel):
    job_id: int
    outcome: str  # judged | already_judged | failed
    title: str | None = None
    match_score: int | None = None
    explanation: str | None = None
    error: str | None = None


@tool_spec(
    name="judge_job",
    description=(
        "Have the LLM judge one job against the user's resume: a verdict (Covered, Augmentable, "
        "Reframe, Gap) with quoted evidence for every required and preferred qualification, rolled up "
        "into a 0-100 match score and a short explanation. Slow (20-60 seconds per job)."
    ),
)
def judge_job(args: JudgeJobInput) -> JudgeResult:
    uid = resolve_user(args.user_id)
    with session() as s:
        row = s.execute(select(Job, Company.name).join(Company, Company.id == Job.company_id).where(Job.id == args.job_id)).first()
        if row is None:
            return JudgeResult(job_id=args.job_id, outcome="failed", error="no such job")
        job, company = row
        mine = s.get(UserJob, (uid, job.id))
        if mine and mine.match_score is not None and not args.force:
            return JudgeResult(job_id=job.id, outcome="already_judged", title=job.title, match_score=mine.match_score,
                               explanation=mine.match_explanation)
        sha = s.scalar(select(GoldenChunk.source_sha).where(GoldenChunk.user_id == uid).limit(1))
        if sha is None:
            return JudgeResult(job_id=job.id, outcome="failed", title=job.title, error="the user's resume is not indexed")
        fit_details = (mine.fit_details if mine else None) or {}
        reqs = _requirements(job)
        if not reqs and job.extracted is None:
            s.expunge(job)
    if not reqs and job.extracted is None:  # no usable bullets: fall back to the LLM extraction
        if enrich_job(EnrichJobInput(job_id=args.job_id)).outcome == "failed":
            return JudgeResult(job_id=args.job_id, outcome="failed", title=job.title, error="could not extract requirements")
        with session() as s:
            job = s.get(Job, args.job_id)
            reqs = _requirements(job)
    if not reqs:
        return JudgeResult(job_id=args.job_id, outcome="failed", title=job.title, error="no requirements found in the posting")
    title, parsed = job.title, ParsedJob.model_validate(job.parsed) if job.parsed else None
    try:
        items = judge(title, company, reqs, uid, model=get_chat_model(user_id=uid))
    except Exception as e:  # noqa: BLE001 - one bad job must not stop the batch
        return JudgeResult(job_id=args.job_id, outcome="failed", title=title, error=f"{type(e).__name__}: {e}"[:300])

    # roll-up in plain code, with the same weights and gates as the fast score
    cfg = get_profile(uid)["targets"]
    req = [i["pct"] for i in items if i["label"] == "required"]
    pref = [i["pct"] for i in items if i["label"] == "preferred"]
    m = sum(req) / len(req) / 100 if req else 0.0
    p = sum(pref) / len(pref) / 100 if pref else m
    comps = fit_details.get("components") or {}
    f, sen, d = comps.get("title_family", 0.0), comps.get("seniority", 0.5), comps.get("domain", 0.0)
    gates = fit_details.get("gates") or []
    score = combine(m, p, f, sen, d, cfg.get("weights"), gates)
    explanation = _explanation(score, items, parsed.seniority if parsed else "unknown", sen, gates)
    now = datetime.now(timezone.utc)
    with session() as s, s.begin():
        row = user_job(s, uid, args.job_id)
        row.match_score, row.match_explanation = score, explanation
        row.match_details = {"items": items, "components": {"required": round(m, 2), "preferred": round(p, 2),
                             "title_family": f, "seniority": sen, "domain": d}, "gates": gates,
                             "requirements_from": "parser" if parsed and parsed.sections_quality == "labelled" else "llm"}
        row.match_model, row.golden_source_sha, row.scored_at = model_spec(uid), sha, now
    return JudgeResult(job_id=args.job_id, outcome="judged", title=title, match_score=score, explanation=explanation)


class JudgeJobsInput(BaseModel):
    user_id: int | None = Field(None, description="Whose jobs to judge; default: the current user")
    min_fit_score: int = Field(50, ge=0, le=100, description="Only jobs with at least this fast score")
    limit: int = Field(20, ge=1, description="Maximum jobs to judge in this call")
    rejudge: bool = Field(False, description="Also judge jobs that already have a match score (e.g. after the resume changed)")


class JudgeJobsResult(BaseModel):
    judged: int = 0
    already_judged: int = 0
    failed: int = 0
    remaining: int = 0          # candidates still without a match score
    scores: list[str] = []      # "score title" for what was judged this call
    error: str | None = None


def _candidates(uid: int, min_fit: int):
    return (
        select(UserJob.job_id).join(Job, Job.id == UserJob.job_id)
        .where(UserJob.user_id == uid, UserJob.in_scope, Job.duplicate_of.is_(None),
               UserJob.fit_score >= min_fit,
               (UserJob.user_label.is_(None)) | (UserJob.user_label != "not_interested"))
    )


@tool_spec(
    name="judge_jobs",
    description=(
        "Run the LLM judgement on the jobs that earned it: in the user's scope, not merged, not marked "
        "not-interested, fast score at or above min_fit_score, favorites and interested jobs first, up to "
        "a limit. Skips jobs already judged unless rejudge is set."
    ),
)
def judge_jobs(args: JudgeJobsInput) -> JudgeJobsResult:
    uid = resolve_user(args.user_id)
    out = JudgeJobsResult()
    limit = args.limit
    if user_llm(uid) is None:  # on the shared model, each user gets a daily budget
        budget = int((get_profile(uid)["llm"] or {}).get("judge_budget_per_day") or 0)
        if budget:
            today = datetime.combine(datetime.now(timezone.utc).date(), _time.min, tzinfo=timezone.utc)
            with session() as s:
                used = s.scalar(select(func.count()).select_from(UserJob).where(
                    UserJob.user_id == uid, UserJob.scored_at >= today))
            limit = min(limit, budget - used)
            if limit <= 0:
                out.error = f"daily judgement budget of {budget} used up; connect your own Claude or OpenAI account to lift it"
                return out
    base = _candidates(uid, args.min_fit_score)
    if not args.rejudge:
        base = base.where(UserJob.match_score.is_(None))
    with session() as s:
        ids = list(s.scalars(base.order_by(
            UserJob.favorite.desc(), (UserJob.user_label == "interested").desc(), UserJob.fit_score.desc(), UserJob.job_id
        ).limit(limit)))
    for n, job_id in enumerate(ids, 1):
        r = judge_job(JudgeJobInput(user_id=uid, job_id=job_id, force=args.rejudge))
        setattr(out, r.outcome, getattr(out, r.outcome) + 1) if r.outcome in ("judged", "already_judged", "failed") else None
        if r.outcome == "judged":
            out.scores.append(f"{r.match_score} {r.title}")
        log.info("judge %d/%d %-14s %s%s", n, len(ids), r.outcome, (r.title or "")[:60],
                 f" -> {r.match_score}" if r.match_score is not None else f" ({r.error})" if r.error else "")
    with session() as s:
        out.remaining = s.scalar(select(func.count()).select_from(
            _candidates(uid, args.min_fit_score).where(UserJob.match_score.is_(None)).subquery()))
    return out


__all__ = ["in_scope", "judge_job", "judge_jobs"]
