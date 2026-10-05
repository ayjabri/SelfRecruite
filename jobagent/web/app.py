"""Local review UI: browse scored jobs and mark the ones you are interested in."""
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from jobagent.db import session
from jobagent.models import Company, Job
from jobagent.tools.scoring import LabelJobInput, in_scope, label_job

app = FastAPI(title="jobagent review")
INDEX = Path(__file__).with_name("index.html")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX.read_text()


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


@app.get("/api/jobs")
def list_jobs(
    q: str = "", company: str = "", min_score: int = 0,
    label: Literal["all", "unlabelled", "interested", "not_interested"] = "all",
    scope: Literal["in_scope", "all"] = "in_scope",
    sort: Literal["score", "newest"] = "score", limit: int = 300,
):
    with session() as s:
        query = select(Job, Company.name).join(Company, Company.id == Job.company_id)
        if scope == "in_scope":
            query = query.where(in_scope())
        if q:
            query = query.where(or_(Job.title.ilike(f"%{q}%"), Job.description_text.ilike(f"%{q}%")))
        if company:
            query = query.where(Company.name == company)
        if min_score:
            query = query.where(Job.fit_score >= min_score)
        if label == "unlabelled":
            query = query.where(Job.user_label.is_(None))
        elif label != "all":
            query = query.where(Job.user_label == label)
        order = (Job.fit_score.desc().nulls_last(), Job.id) if sort == "score" else (Job.first_seen_at.desc(), Job.id)
        total = s.scalar(select(func.count()).select_from(query.subquery()))
        rows = s.execute(query.order_by(*order).limit(min(limit, 1000))).all()
        return {"total": total, "jobs": [{
            "id": j.id, "title": j.title, "company": name, "location": _location_text(j.locations),
            "workplace": j.workplace_type, "seniority": j.seniority, "fit_score": j.fit_score,
            "gated": bool((j.fit_details or {}).get("gates")), "label": j.user_label,
            "comp_min": float(j.comp_min) if j.comp_min is not None else None,
            "comp_max": float(j.comp_max) if j.comp_max is not None else None,
            "comp_period": j.comp_period,
        } for j, name in rows]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    with session() as s:
        row = s.execute(
            select(Job, Company.name).join(Company, Company.id == Job.company_id).where(Job.id == job_id)
        ).first()
        if row is None:
            raise HTTPException(404, "no such job")
        j, name = row
        parsed = j.parsed or {}
        return {
            "id": j.id, "title": j.title, "company": name, "url": j.apply_url,
            "location": _location_text(j.locations), "workplace": j.workplace_type, "seniority": j.seniority,
            "posted": j.source_posted_at.date().isoformat() if j.source_posted_at else None,
            "first_seen": j.first_seen_at.date().isoformat(),
            "comp_min": float(j.comp_min) if j.comp_min is not None else None,
            "comp_max": float(j.comp_max) if j.comp_max is not None else None,
            "comp_period": j.comp_period, "comp_text": parsed.get("comp_text"),
            "min_years": parsed.get("min_years_experience"), "education": parsed.get("education"),
            "employment_type": parsed.get("employment_type"), "visa": parsed.get("visa_sponsorship"),
            "fit_score": j.fit_score, "fit_details": j.fit_details, "label": j.user_label,
            "sections": parsed.get("sections") or [], "description": j.description_text,
        }


class LabelBody(BaseModel):
    label: Literal["interested", "not_interested", "clear"]


@app.post("/api/jobs/{job_id}/label")
def set_label(job_id: int, body: LabelBody):
    result = label_job(LabelJobInput(job_id=job_id, label=body.label))
    if result.error:
        raise HTTPException(404, result.error)
    return result


@app.get("/api/summary")
def summary():
    with session() as s:
        scoped = select(Job.id, Job.user_label, Job.company_id).where(in_scope()).subquery()
        counts = dict(s.execute(select(scoped.c.user_label, func.count()).group_by(scoped.c.user_label)).all())
        companies = s.scalars(
            select(Company.name).where(Company.id.in_(select(scoped.c.company_id))).order_by(Company.name)
        ).all()
        return {
            "in_scope": sum(counts.values()), "interested": counts.get("interested", 0),
            "not_interested": counts.get("not_interested", 0), "companies": companies,
        }
