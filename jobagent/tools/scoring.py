"""Fast score: rules plus embedding similarity against the golden source. No LLM."""
import logging
import re
from datetime import datetime, timezone
from typing import Literal

import httpx
import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from jobagent.config import load_targets
from jobagent.db import session
from jobagent.models import Company, GoldenChunk, Job, JobSource, RawPosting
from jobagent.parse.schema import ParsedJob
from jobagent.tools.embeddings import QUERY, embed_texts
from jobagent.tools.filtering import KEPT
from jobagent.tools.geo import phrase_regex
from jobagent.tools.preference import example_from, load_model
from jobagent.tools.registry import tool_spec

log = logging.getLogger(__name__)

GATE_CAP = 30
DEFAULT_WEIGHTS = {"required": 0.45, "preferred": 0.20, "title_family": 0.15, "seniority": 0.10, "domain": 0.10}


def in_scope():
    """SQL condition: the job has at least one source posting the pre-filter kept."""
    return (
        select(JobSource.job_id).join(RawPosting, RawPosting.id == JobSource.raw_id)
        .where(JobSource.job_id == Job.id, RawPosting.filter_status == KEPT).exists()
    )


def rescale(sim: float, floor: float, ceiling: float) -> float:
    return float(min(1.0, max(0.0, (sim - floor) / (ceiling - floor))))


def title_family(title: str, families: dict) -> tuple[float, str | None]:
    best, name = 0.0, None
    for family, cfg in (families or {}).items():
        rx = phrase_regex(cfg.get("keywords") or [])
        if rx and rx.search(title) and float(cfg.get("weight", 1.0)) > best:
            best, name = float(cfg.get("weight", 1.0)), family
    return best, name


def domain_fit(company: str, title: str, bullets: list[str], domains: dict) -> float:
    if company.lower() in {c.lower() for c in domains.get("companies") or []}:
        return 1.0
    text = " ".join([title, *bullets]).lower()
    hits = sum(1 for k in domains.get("keywords") or [] if k.lower() in text)
    return min(1.0, hits / 3)


_PHD = re.compile(r"\bph\.?\s?d\b|\bdoctorate\b", re.I)
# "BS, MS, or PhD" and "PhD preferred" are not PhD requirements
_PHD_ALTERNATIVE = re.compile(
    r"\bbachelor|\bmaster|\b[BM]\.?[SA]\.?\b|\b[BM]SEE\b|\bMBA\b|\bequivalent\b|\bpreferred\b|\bplus\b|\bnice to have\b", re.I)


def gates(parsed: ParsedJob, comp_max, cfg: dict) -> list[str]:
    out = []
    if cfg.get("clearance_required") and parsed.clearance_required:
        out.append("security clearance required")
    if cfg.get("phd_required") and any(_PHD.search(b) and not _PHD_ALTERNATIVE.search(b) for b in parsed.requirements):
        out.append("PhD required")
    floor = cfg.get("pay_floor")
    if floor and comp_max is not None and parsed.comp_period == "year" and float(comp_max) < float(floor):
        out.append(f"top of pay range below {int(floor):,}")
    allowed = {s.upper() for s in cfg.get("allowed_states") or []}
    states = {loc.state for loc in parsed.locations if loc.state}
    if allowed and parsed.workplace_type != "remote" and states and not (states & allowed):
        out.append(f"not remote and located in {', '.join(sorted(states))}")
    return out


def combine(m: float, p: float, f: float, s: float, d: float, weights: dict, tripped: list[str]) -> int:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    score = 100 * (w["required"] * m + w["preferred"] * p + w["title_family"] * f
                   + w["seniority"] * s + w["domain"] * d)
    return round(min(score, GATE_CAP) if tripped else score)


class ScoreJobsInput(BaseModel):
    rescore: bool = Field(False, description="Score again jobs that already have a fast score, e.g. after config/targets.yaml changed")
    scope: Literal["in_scope", "all"] = Field("in_scope", description="'in_scope' scores only jobs whose posting passed the pre-filter")
    limit: int | None = Field(None, ge=1, description="Maximum jobs to score in this call; omit for all")


class ScoreJobsResult(BaseModel):
    scored: int = 0
    gated: int = 0
    reused_similarities: int = 0   # jobs whose bullet matches were reused instead of re-embedded
    used_preference_model: bool = False   # True when scores come from the model trained on your marks
    score_min: int | None = None
    score_median: int | None = None
    score_max: int | None = None
    remaining: int = 0
    error: str | None = None


@tool_spec(
    name="score_jobs_fast",
    description=(
        "Compute the fast fit score (0-100) for jobs, with no LLM. Once a preference model has been "
        "trained on the user's marks, the score is that model's estimate of interest; before that it is "
        "a rule score: embedding similarity between each requirement bullet and the golden-source "
        "resume, plus title family, seniority and domain rules from config/targets.yaml. Hard gates cap "
        "either score at 30. Stores the score and its breakdown on each job."
    ),
)
def score_jobs_fast(args: ScoreJobsInput) -> ScoreJobsResult:
    load_targets.cache_clear()
    cfg = load_targets()
    sim_cfg = cfg.get("similarity") or {}
    floor, ceiling = float(sim_cfg.get("floor", 0.55)), float(sim_cfg.get("ceiling", 0.80))
    out = ScoreJobsResult()

    with session() as s:
        chunks = s.execute(
            select(GoldenChunk.heading, GoldenChunk.embedding, GoldenChunk.source_sha)
            .where(~GoldenChunk.always_include).order_by(GoldenChunk.position)
        ).all()
        if not chunks:
            out.error = "golden source is not indexed; run index_golden_source first"
            return out
        sha = chunks[0].source_sha
        query = (
            select(Job.id, Job.title, Job.parsed, Job.comp_max, Job.embedding, Job.fit_details, Company.name)
            .join(Company, Company.id == Job.company_id)
            .where(Job.parsed.is_not(None), Job.duplicate_of.is_(None)).order_by(Job.id)
        )
        if args.scope == "in_scope":
            query = query.where(in_scope())
        if not args.rescore:
            query = query.where(Job.fit_score.is_(None))
        jobs = s.execute(query.limit(args.limit)).all()

    headings = [c.heading for c in chunks]
    G = np.array([c.embedding for c in chunks], dtype=np.float32)
    G /= np.linalg.norm(G, axis=1, keepdims=True)

    def best_matches(vectors) -> list[tuple[float, str]]:
        V = np.array(vectors, dtype=np.float32)
        V /= np.linalg.norm(V, axis=1, keepdims=True)
        sims = V @ G.T
        idx = sims.argmax(axis=1)
        return [(float(sims[i, j]), headings[j]) for i, j in enumerate(idx)]

    # gather the bullets that need embedding; reuse stored matches when the resume is unchanged
    work, to_embed = [], []
    for job in jobs:
        parsed = ParsedJob.model_validate(job.parsed)
        bullets = [("required", b) for b in parsed.requirements] + [("preferred", b) for b in parsed.bullets("preferred")]
        old = job.fit_details or {}
        reusable = (
            old.get("golden_source_sha") == sha
            and [(b["label"], b["text"]) for b in old.get("bullets", [])] == bullets
        )
        if reusable:
            matches = [(b["similarity"], b["matched"]) for b in old["bullets"]]
            out.reused_similarities += 1
        else:
            matches = None
            to_embed.extend(text for _, text in bullets)
        work.append((job, parsed, bullets, matches))

    try:
        fresh = iter(best_matches(embed_texts(to_embed, QUERY)) if to_embed else [])
    except httpx.HTTPError as e:
        out.error = f"embedding failed: {type(e).__name__}: {e}"
        return out

    scores, now = [], datetime.now(timezone.utc).isoformat()
    updates = []
    for job, parsed, bullets, matches in work:
        if matches is None:
            matches = [next(fresh) for _ in bullets]
        detail = [
            {"label": label, "text": text, "similarity": round(sim, 3), "matched": heading,
             "score": round(rescale(sim, floor, ceiling), 2)}
            for (label, text), (sim, heading) in zip(bullets, matches)
        ]
        req = [b["score"] for b in detail if b["label"] == "required"]
        pref = [b["score"] for b in detail if b["label"] == "preferred"]
        if req:
            m = sum(req) / len(req)
        elif job.embedding is not None:  # no bullets: compare the whole posting with the resume
            m = rescale(best_matches([job.embedding])[0][0], floor, ceiling)
        else:
            m = 0.0
        p = sum(pref) / len(pref) if pref else m
        f, family = title_family(job.title, cfg.get("title_families"))
        sen = float((cfg.get("seniority") or {}).get(parsed.seniority, 0.5))
        d = domain_fit(job.name, job.title, [t for _, t in bullets], cfg.get("domains") or {})
        tripped = gates(parsed, job.comp_max, cfg.get("gates") or {})
        rule_score = combine(m, p, f, sen, d, cfg.get("weights"), [])
        scores.append(rule_score)
        out.gated += bool(tripped)
        updates.append((job.id, rule_score, {
            "rule_score": rule_score, "embedding": job.embedding,
            "components": {"required": round(m, 2), "preferred": round(p, 2), "title_family": round(f, 2),
                           "seniority": round(sen, 2), "domain": round(d, 2)},
            "title_family": family, "seniority": parsed.seniority, "gates": tripped,
            "sections_quality": parsed.sections_quality, "bullets": detail,
            "golden_source_sha": sha, "scored_at": now,
        }))

    # with a trained preference model, the score is its probability that you would be interested;
    # the rule score stays in the details as the explanation
    model = load_model()
    if model and updates:
        examples = [
            example_from(job.title, job.name, parsed.seniority, details, details["embedding"])
            for (job, parsed, _, _), (_, _, details) in zip(work, updates)
        ]
        for (_, _, details), prob in zip(updates, model.predict(examples)):
            details["preference"] = round(prob, 3)
            details["model"] = model.meta
        out.used_preference_model = True
    scores = []
    with session() as s, s.begin():
        for job_id, rule_score, details in updates:
            details.pop("embedding")
            score = round(100 * details["preference"]) if "preference" in details else rule_score
            if details["gates"]:
                score = min(score, GATE_CAP)
            scores.append(score)
            job = s.get(Job, job_id)
            job.fit_score, job.fit_details = score, details
    with session() as s:
        remaining = select(func.count()).select_from(Job).where(
            Job.fit_score.is_(None), Job.parsed.is_not(None), Job.duplicate_of.is_(None))
        if args.scope == "in_scope":
            remaining = remaining.where(in_scope())
        out.remaining = s.scalar(remaining)
    out.scored = len(scores)
    if scores:
        out.score_min, out.score_max = min(scores), max(scores)
        out.score_median = int(np.median(scores))
    log.info("fast score: %d jobs scored, %d gated", out.scored, out.gated)
    return out


class LabelJobInput(BaseModel):
    job_id: int = Field(description="jobs.id to label")
    label: Literal["interested", "not_interested", "clear"] = Field(description="Your verdict; 'clear' removes it")


class LabelJobResult(BaseModel):
    job_id: int
    label: str | None = None
    error: str | None = None


@tool_spec(
    name="label_job",
    description="Record whether the user is interested in a job. Labels are used to calibrate the fit score.",
)
def label_job(args: LabelJobInput) -> LabelJobResult:
    with session() as s, s.begin():
        job = s.get(Job, args.job_id)
        if job is None:
            return LabelJobResult(job_id=args.job_id, error="no such job")
        job.user_label = None if args.label == "clear" else args.label
        job.user_labeled_at = None if args.label == "clear" else datetime.now(timezone.utc)
        return LabelJobResult(job_id=job.id, label=job.user_label)
