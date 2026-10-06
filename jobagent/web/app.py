"""Review UI: browse scored jobs and mark the ones you are interested in.

Every request acts as one user. Until sign-in exists that is DEFAULT_USER_ID (see jobagent.users);
`current_user()` is the single place to swap in a session lookup later.
"""
import io
import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from jobagent.config import ROOT
from jobagent.db import session
from jobagent.models import Company, Job, JobSource, PossibleDuplicate, RawPosting, UserJob
from jobagent.skills import skill_gap
from jobagent.tools import TOOLS
from jobagent.tools.common import NORMALIZERS
from jobagent.tools.dedup import DecideDuplicateInput, decide_duplicate
from jobagent.tools.scoring import AnnotateJobInput, LabelJobInput, annotate_job, in_scope, label_job
from jobagent.users import DEFAULT_USER_ID
from jobagent.web import configs, runs

app = FastAPI(title="jobagent review")
INDEX = Path(__file__).with_name("index.html")


def current_user() -> int:
    return DEFAULT_USER_ID


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX.read_text()


LOGO_SOURCE = ROOT / "docs" / "phoenix1.png"


@lru_cache
def _logo_bytes() -> bytes:
    """Crop the logo to its visible content and shrink it, once per server start."""
    from PIL import Image, ImageChops
    im = Image.open(LOGO_SOURCE).convert("RGBA")
    alpha = im.getchannel("A")
    if alpha.getextrema()[0] < 255:  # transparent background: crop to the opaque pixels
        bbox = alpha.point(lambda v: 255 if v > 16 else 0).getbbox()
    else:  # opaque black background: crop to what is not black
        rgb = im.convert("RGB")
        bbox = ImageChops.difference(rgb, Image.new("RGB", rgb.size, (0, 0, 0))).point(lambda v: 255 if v > 24 else 0).getbbox()
    crop = im.crop(bbox) if bbox else im
    height = 160
    crop = crop.resize((round(crop.width * height / crop.height), height), Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, "PNG", optimize=True)
    return buf.getvalue()


@app.get("/logo.png")
def logo():
    if not LOGO_SOURCE.exists():
        raise HTTPException(404, "docs/phoenix1.png not found")
    return Response(_logo_bytes(), media_type="image/png")


def _location_text(locations) -> str:
    parts = []
    for loc in locations or []:
        bits = [loc.get("city"), loc.get("state")]
        if loc.get("country") and loc.get("country") != "US":
            bits.append(loc["country"])
        text = ", ".join(b for b in bits if b)
        if text and text not in parts:
            parts.append(text)
    return " · ".join(parts[:4]) + (f" +{len(parts) - 4}" if len(parts) > 4 else "")


def _mine(uid: int):
    """Join condition for the current user's row on a job (may be absent)."""
    return (UserJob.job_id == Job.id) & (UserJob.user_id == uid)


@app.get("/api/jobs")
def list_jobs(
    q: str = "", company: str = "", min_score: int = 0,
    label: Literal["all", "unlabelled", "interested", "not_interested"] = "all",
    flag: Literal["any", "favorite", "applied", "not_applied"] = "any",
    scope: Literal["in_scope", "all"] = "in_scope",
    posted: Literal["any", "24h", "7d", "30d"] = "any",
    sort: Literal["score", "newest", "match"] = "score", limit: int = 300,
):
    uid = current_user()
    with session() as s:
        query = (select(Job, Company.name, UserJob).join(Company, Company.id == Job.company_id)
                 .outerjoin(UserJob, _mine(uid)).where(Job.duplicate_of.is_(None)))
        if scope == "in_scope":
            query = query.where(in_scope(uid))
        if q:
            query = query.where(or_(Job.title.ilike(f"%{q}%"), Job.description_text.ilike(f"%{q}%")))
        if company:
            query = query.where(Company.name == company)
        if min_score:
            query = query.where(UserJob.fit_score >= min_score)
        if posted != "any":
            # the source's own posting date when it gives one, else when we first saw the job
            since = datetime.now(timezone.utc) - timedelta(**{"24h": {"hours": 24}, "7d": {"days": 7}, "30d": {"days": 30}}[posted])
            query = query.where(func.coalesce(Job.source_posted_at, Job.first_seen_at) >= since)
        if flag == "favorite":
            query = query.where(UserJob.favorite)
        elif flag == "applied":
            query = query.where(UserJob.applied_at.is_not(None))
        elif flag == "not_applied":
            query = query.where(UserJob.applied_at.is_(None))
        if label == "unlabelled":
            query = query.where(UserJob.user_label.is_(None))
        elif label != "all":
            query = query.where(UserJob.user_label == label)
        newest = func.coalesce(Job.source_posted_at, Job.first_seen_at).desc()
        order = {"score": (UserJob.fit_score.desc().nulls_last(), Job.id), "newest": (newest, Job.id),
                 "match": (UserJob.match_score.desc().nulls_last(), UserJob.fit_score.desc().nulls_last(), Job.id)}[sort]
        total = s.scalar(select(func.count()).select_from(query.subquery()))
        rows = s.execute(query.order_by(*order).limit(min(limit, 1000))).all()
        return {"total": total, "jobs": [{
            "id": j.id, "title": j.title, "company": name, "location": _location_text(j.locations),
            "workplace": j.workplace_type, "seniority": j.seniority,
            "fit_score": u.fit_score if u else None,
            "gated": bool(((u.fit_details if u else None) or {}).get("gates")), "label": u.user_label if u else None,
            "posted": (j.source_posted_at or j.first_seen_at).date().isoformat(),
            "favorite": bool(u and u.favorite), "applied": bool(u and u.applied_at), "has_notes": bool(u and u.notes),
            "match_score": u.match_score if u else None,
            "comp_min": float(j.comp_min) if j.comp_min is not None else None,
            "comp_max": float(j.comp_max) if j.comp_max is not None else None,
            "comp_period": j.comp_period,
        } for j, name, u in rows]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    uid = current_user()
    with session() as s:
        row = s.execute(
            select(Job, Company.name, UserJob).join(Company, Company.id == Job.company_id)
            .outerjoin(UserJob, _mine(uid)).where(Job.id == job_id)
        ).first()
        if row is None:
            raise HTTPException(404, "no such job")
        j, name, u = row
        parsed = j.parsed or {}
        members = [j.id, *s.scalars(select(Job.id).where(Job.duplicate_of == j.id))]
        postings = s.execute(
            select(RawPosting.url, RawPosting.source, RawPosting.target, RawPosting.payload, JobSource.job_id)
            .join(JobSource, JobSource.raw_id == RawPosting.id).where(JobSource.job_id.in_(members))
            .order_by(RawPosting.id)
        ).all()
        return {
            "postings": [{"url": p.url, "source": p.source, "target": p.target,
                          "title": (NORMALIZERS[p.source](p.payload).title if p.source in NORMALIZERS else None),
                          "location": (NORMALIZERS[p.source](p.payload).location_text if p.source in NORMALIZERS else "")}
                         for p in postings],
            "id": j.id, "title": j.title, "company": name, "url": j.apply_url,
            "requisition_id": j.requisition_id,
            "location": _location_text(j.locations), "workplace": j.workplace_type, "seniority": j.seniority,
            "posted": j.source_posted_at.date().isoformat() if j.source_posted_at else None,
            "first_seen": j.first_seen_at.date().isoformat(),
            "comp_min": float(j.comp_min) if j.comp_min is not None else None,
            "comp_max": float(j.comp_max) if j.comp_max is not None else None,
            "comp_period": j.comp_period, "comp_text": parsed.get("comp_text"),
            "min_years": parsed.get("min_years_experience"), "education": parsed.get("education"),
            "employment_type": parsed.get("employment_type"), "visa": parsed.get("visa_sponsorship"),
            "in_scope": bool(u and u.in_scope),
            "fit_score": u.fit_score if u else None, "fit_details": u.fit_details if u else None,
            "label": u.user_label if u else None,
            "favorite": bool(u and u.favorite),
            "applied_at": u.applied_at.date().isoformat() if u and u.applied_at else None,
            "notes": (u.notes if u else None) or "",
            "match_score": u.match_score if u else None, "match_explanation": u.match_explanation if u else None,
            "match_details": u.match_details if u else None,
            "scored_at": u.scored_at.date().isoformat() if u and u.scored_at else None,
            "sections": parsed.get("sections") or [], "description": j.description_text,
            "skills": skill_gap(j.description_text or "", uid),
        }


class LabelBody(BaseModel):
    label: Literal["interested", "not_interested", "clear"]


@app.post("/api/jobs/{job_id}/label")
def set_label(job_id: int, body: LabelBody):
    result = label_job(LabelJobInput(user_id=current_user(), job_id=job_id, label=body.label))
    if result.error:
        raise HTTPException(404, result.error)
    return result


class AnnotateBody(BaseModel):
    favorite: bool | None = None
    applied: bool | None = None
    notes: str | None = None


@app.post("/api/jobs/{job_id}/annotate")
def set_annotations(job_id: int, body: AnnotateBody):
    result = annotate_job(AnnotateJobInput(user_id=current_user(), job_id=job_id, **body.model_dump()))
    if result.error:
        raise HTTPException(404, result.error)
    return result


@app.get("/api/duplicates")
def list_duplicates():
    """Pending possible-duplicate pairs, with enough of each job (and your marks on it) to judge."""
    uid = current_user()
    with session() as s:
        A, B = Job.__table__.alias("a"), Job.__table__.alias("b")
        UA, UB = UserJob.__table__.alias("ua"), UserJob.__table__.alias("ub")
        rows = s.execute(
            select(PossibleDuplicate.id, PossibleDuplicate.similarity, Company.name,
                   A.c.id, A.c.title, A.c.locations, UA.c.fit_score, UA.c.user_label,
                   B.c.id, B.c.title, B.c.locations, UB.c.fit_score, UB.c.user_label)
            .join(A, A.c.id == PossibleDuplicate.job_a).join(B, B.c.id == PossibleDuplicate.job_b)
            .join(Company, Company.id == A.c.company_id)
            .outerjoin(UA, (UA.c.job_id == A.c.id) & (UA.c.user_id == uid))
            .outerjoin(UB, (UB.c.job_id == B.c.id) & (UB.c.user_id == uid))
            .where(PossibleDuplicate.status == "pending", A.c.duplicate_of.is_(None), B.c.duplicate_of.is_(None))
            .order_by(PossibleDuplicate.similarity.desc())
        ).all()
        return [{
            "id": r[0], "similarity": float(r[1]), "company": r[2],
            "a": {"id": r[3], "title": r[4], "location": _location_text(r[5]), "fit_score": r[6], "label": r[7]},
            "b": {"id": r[8], "title": r[9], "location": _location_text(r[10]), "fit_score": r[11], "label": r[12]},
        } for r in rows]


class DecisionBody(BaseModel):
    decision: Literal["same", "different"]


class BulkDecisionBody(DecisionBody):
    pair_ids: list[int]


@app.post("/api/duplicates/bulk")
def resolve_duplicates(body: BulkDecisionBody):
    """Apply one decision to several pairs; pairs that no longer exist are reported, not fatal."""
    results = [decide_duplicate(DecideDuplicateInput(pair_id=i, decision=body.decision)) for i in body.pair_ids]
    return {"decided": [r.pair_id for r in results if not r.error],
            "errors": {r.pair_id: r.error for r in results if r.error}}


@app.post("/api/duplicates/{pair_id}")
def resolve_duplicate(pair_id: int, body: DecisionBody):
    result = decide_duplicate(DecideDuplicateInput(pair_id=pair_id, decision=body.decision))
    if result.error:
        raise HTTPException(404, result.error)
    return result


@app.get("/api/summary")
def summary():
    uid = current_user()
    with session() as s:
        A, B = Job.__table__.alias("a"), Job.__table__.alias("b")
        pending = s.scalar(  # pairs still open whose jobs were not merged some other way since
            select(func.count()).select_from(PossibleDuplicate)
            .join(A, A.c.id == PossibleDuplicate.job_a).join(B, B.c.id == PossibleDuplicate.job_b)
            .where(PossibleDuplicate.status == "pending", A.c.duplicate_of.is_(None), B.c.duplicate_of.is_(None)))
        scoped = (select(Job.id, UserJob.user_label, Job.company_id, UserJob.favorite, UserJob.applied_at)
                  .join(UserJob, _mine(uid)).where(UserJob.in_scope, Job.duplicate_of.is_(None)).subquery())
        counts = dict(s.execute(select(scoped.c.user_label, func.count()).group_by(scoped.c.user_label)).all())
        favorites = s.scalar(select(func.count()).select_from(scoped).where(scoped.c.favorite))
        applied = s.scalar(select(func.count()).select_from(scoped).where(scoped.c.applied_at.is_not(None)))
        companies = s.scalars(
            select(Company.name).where(Company.id.in_(select(scoped.c.company_id))).order_by(Company.name)
        ).all()
        return {
            "in_scope": sum(counts.values()), "interested": counts.get("interested", 0),
            "not_interested": counts.get("not_interested", 0), "companies": companies,
            "pending_duplicates": pending, "favorites": favorites, "applied": applied,
        }


# --- running jobagent commands from the page -------------------------------------------------

@app.get("/api/tools")
def list_tools():
    """Registered tools with their input schema, so the page can build a form for each."""
    return [{
        "name": spec.name, "description": spec.description,
        "fields": [{
            "name": name, "description": field.description or "",
            "type": _field_type(field.annotation), "default": field.default if field.default is not None else None,
            "required": field.is_required(),
        } for name, field in spec.input_model.model_fields.items() if name != "user_id"],
    } for spec in TOOLS.values()]


def _field_type(annotation) -> str:
    text = str(annotation)
    if "bool" in text:
        return "bool"
    if "int" in text or "float" in text:
        return "number"
    if "Literal" in text:
        return "choice:" + ",".join(str(a) for a in getattr(annotation, "__args__", ()))
    return "text"


class RunBody(BaseModel):
    command: Literal["pipeline", "run"]
    request: str | None = None            # pipeline: the orchestrator's instruction
    tool: str | None = None               # run: the tool name
    args: dict[str, object] = {}          # run: its arguments


@app.post("/api/runs")
def start_run(body: RunBody):
    if body.command == "pipeline":
        argv = ["pipeline", body.request or "Daily pull of all configured targets."]
        label = f"pipeline: {argv[1][:80]}"
    else:
        if body.tool not in TOOLS:
            raise HTTPException(400, "unknown tool")
        clean = {k: v for k, v in body.args.items() if v not in (None, "", []) and k != "user_id"}
        if "user_id" in TOOLS[body.tool].input_model.model_fields:
            clean["user_id"] = current_user()
        argv = ["run", body.tool, "--json", json.dumps(clean)]
        label = f"{body.tool} {json.dumps({k: v for k, v in clean.items() if k != 'user_id'})}"[:120]
    try:
        return runs.start(argv, label)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e


@app.get("/api/runs")
def list_runs():
    return {"running": (r := runs.running()) and {k: v for k, v in r.items() if k != "proc"}, "runs": runs.load_runs()}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(404, "no such run")
    return run


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str):
    return {"stopped": runs.stop(run_id)}


# --- settings: profile sections and the shared config files ----------------------------------

@app.get("/api/config")
def list_config():
    return configs.read_all(current_user())


@app.get("/api/config/{name}")
def get_config(name: str):
    try:
        return {**configs.read(name, current_user()), "backups": configs.backups(name)}
    except KeyError:
        raise HTTPException(404, "no such setting")


class ConfigBody(BaseModel):
    text: str | None = None            # raw YAML, saved as typed (files only)
    data: dict[str, object] | None = None   # form values


@app.put("/api/config/{name}")
def put_config(name: str, body: ConfigBody):
    try:
        if body.text is not None:
            if name in configs.PROFILE_SECTIONS:
                raise HTTPException(400, "profile sections are edited with the form")
            return configs.save_text(name, body.text)
        if body.data is not None:
            return configs.save_data(name, body.data, current_user())
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(404, "no such setting")
    except Exception as e:  # bad YAML or unwritable file: tell the page why
        raise HTTPException(400, f"{type(e).__name__}: {e}")
    raise HTTPException(400, "send text or data")


# --- the current user's resume -------------------------------------------------------------

@app.get("/api/resume")
def get_resume():
    from jobagent.users import active_resume
    with session() as s:
        r = active_resume(s, current_user())
        return {"filename": r.filename, "sha": r.sha, "uploaded_at": r.uploaded_at.isoformat(timespec="seconds"),
                "chars": len(r.text), "text": r.text} if r else None


class ResumeBody(BaseModel):
    text: str
    filename: str | None = None


@app.put("/api/resume")
def put_resume(body: ResumeBody):
    from jobagent.tools.embeddings import UploadResumeInput, upload_resume
    result = upload_resume(UploadResumeInput(user_id=current_user(), text=body.text, filename=body.filename))
    if result.error:
        raise HTTPException(400, result.error)
    return result
