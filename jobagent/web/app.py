"""Review UI: browse scored jobs and mark the ones you are interested in.

Every request acts as the signed-in user. A middleware resolves the session cookie once per
request and `current_user()` reads it; admin-only endpoints call `require_admin()`.
"""
import contextvars
import io
import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select

from jobagent import auth
from jobagent.config import ROOT
from jobagent.db import session
from jobagent.models import Company, Job, JobSource, PossibleDuplicate, RawPosting, Resume, Run, Session, User, UserJob
from jobagent.skills import skill_gap
from jobagent.tools import TOOLS
from jobagent.tools.common import NORMALIZERS
from jobagent.tools.dedup import DecideDuplicateInput, decide_duplicate
from jobagent.tools.scoring import AnnotateJobInput, LabelJobInput, annotate_job, in_scope, label_job
from jobagent.web import configs, runs

app = FastAPI(title="jobagent review")
INDEX = Path(__file__).with_name("index.html")
LOGIN = Path(__file__).with_name("login.html")
runs.recover()

_request_user: contextvars.ContextVar[User | None] = contextvars.ContextVar("user", default=None)
_request_csrf: contextvars.ContextVar[str | None] = contextvars.ContextVar("csrf", default=None)
PUBLIC = ("/login", "/logo.png", "/api/auth/status", "/api/auth/login", "/api/auth/register")
app.mount("/static", StaticFiles(directory=str(Path(__file__).with_name("static"))), name="static")


@app.middleware("http")
async def sign_in_gate(request: Request, call_next):
    """Resolve the session cookie; block API calls without one; check CSRF on writes."""
    found = auth.resolve_session(request.cookies.get(auth.COOKIE))
    user, csrf = found if found else (None, None)
    _request_user.set(user)
    _request_csrf.set(csrf)
    path = request.url.path
    if user is None and path not in PUBLIC and not path.startswith("/static/") and path != "/healthz":
        if path.startswith("/api/"):
            return JSONResponse({"detail": "sign in first"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    if user is not None and path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS") \
            and path not in PUBLIC and request.headers.get("x-csrf-token") != csrf:
        return JSONResponse({"detail": "missing or stale CSRF token; reload the page"}, status_code=403)
    return await call_next(request)


MAX_BODY = 4 * 1024 * 1024  # a resume is well under 1 MB; Caddy enforces the same limit in front


@app.middleware("http")
async def hardening(request: Request, call_next):
    """Body-size limit and the usual security headers on every response."""
    if (length := request.headers.get("content-length")) and int(length) > MAX_BODY:
        return JSONResponse({"detail": "request too large"}, status_code=413)
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path in ("/", "/login"):
        response.headers["Cache-Control"] = "no-cache"  # always revalidate; the ?v= tag makes new files load
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
    return response



def current_user() -> int:
    user = _request_user.get()
    if user is None:
        raise HTTPException(401, "sign in first")
    return user.id


def require_admin() -> User:
    user = _request_user.get()
    if user is None:
        raise HTTPException(401, "sign in first")
    if user.role != "admin":
        raise HTTPException(403, "admin only")
    return user


def _set_cookie(response: Response, token: str, request: Request) -> None:
    secure = auth.COOKIE_SECURE or request.url.scheme == "https"  # the scheme is real behind --proxy-headers
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=secure, path="/")


@app.get("/healthz")
def healthz():
    """For the proxy / a monitor: 200 when the database answers."""
    try:
        with session() as s:
            s.execute(select(1)).scalar()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": type(e).__name__}, status_code=503)
    return {"ok": True}


STATIC = Path(__file__).with_name("static")


@lru_cache(maxsize=8)
def _asset_version(stamp: float) -> str:
    """A short tag that changes whenever any static file changes, so browsers never serve stale scripts."""
    import hashlib
    h = hashlib.sha1()
    for f in sorted(STATIC.iterdir()):
        h.update(f"{f.name}:{f.stat().st_mtime_ns}".encode())
    return h.hexdigest()[:10]


def _page(path: Path) -> str:
    stamp = max(f.stat().st_mtime for f in STATIC.iterdir())
    v = _asset_version(stamp)
    return path.read_text().replace('src="/static/', f'src="/static/').replace('.js"></script>', f'.js?v={v}"></script>') \
        .replace('href="/static/style.css"', f'href="/static/style.css?v={v}"')


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _page(INDEX)


@app.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    return LOGIN.read_text()


# --- auth ------------------------------------------------------------------------------------

class Credentials(BaseModel):
    email: str
    password: str
    name: str | None = None


@app.get("/api/auth/status")
def auth_status():
    return {"signed_in": _request_user.get() is not None, "registration_open": auth.REGISTRATION_OPEN}


def _sign_in(user: User, request: Request) -> JSONResponse:
    token, csrf = auth.create_session(user.id, request.headers.get("user-agent"))
    response = JSONResponse({"id": user.id, "email": user.email, "name": user.name, "role": user.role, "csrf": csrf})
    _set_cookie(response, token, request)
    return response


@app.post("/api/auth/register")
def register(body: Credentials, request: Request):
    try:
        user = auth.register(body.email, body.password, body.name)
    except auth.AuthError as e:
        raise HTTPException(e.status, str(e))
    return _sign_in(user, request)


@app.post("/api/auth/login")
def login(body: Credentials, request: Request):
    try:
        user = auth.authenticate(body.email, body.password, request.client.host if request.client else "")
    except auth.AuthError as e:
        raise HTTPException(e.status, str(e))
    return _sign_in(user, request)


@app.post("/api/auth/logout")
def logout(request: Request):
    auth.destroy_session(request.cookies.get(auth.COOKIE))
    response = JSONResponse({"signed_out": True})
    response.delete_cookie(auth.COOKIE, path="/")
    return response


@app.get("/api/auth/me")
def me():
    user = _request_user.get()
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role, "csrf": _request_csrf.get()}


class PasswordChange(BaseModel):
    current: str
    new: str


@app.post("/api/auth/password")
def change_password(body: PasswordChange, request: Request):
    try:
        auth.change_password(current_user(), body.current, body.new, keep_token=request.cookies.get(auth.COOKIE))
    except auth.AuthError as e:
        raise HTTPException(e.status, str(e))
    return {"changed": True}


class DeleteAccount(BaseModel):
    password: str


@app.post("/api/auth/delete")
def delete_account(body: DeleteAccount, request: Request):
    """Removes the account and everything that is theirs: profile, resumes, resume chunks, marks,
    notes, judgements, preference model, sessions. Shared jobs stay."""
    uid = current_user()
    with session() as s, s.begin():
        user = s.get(User, uid)
        if not auth.verify_password(body.password, user.password_hash):
            raise HTTPException(403, "wrong password")
        admins = s.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.status == "active"))
        if user.role == "admin" and admins <= 1:
            raise HTTPException(400, "you are the only admin; make someone else admin first")
        s.delete(user)  # ON DELETE CASCADE takes the rest
    response = JSONResponse({"deleted": True})
    response.delete_cookie(auth.COOKIE, path="/")
    return response


@app.get("/api/users")
def list_users():
    require_admin()
    with session() as s:
        marks = dict(s.execute(select(UserJob.user_id, func.count()).where(UserJob.user_label.is_not(None)).group_by(UserJob.user_id)).all())
        scoped = dict(s.execute(select(UserJob.user_id, func.count()).where(UserJob.in_scope).group_by(UserJob.user_id)).all())
        resumes = dict(s.execute(select(Resume.user_id, func.count()).where(Resume.active).group_by(Resume.user_id)).all())
        return [{"id": u.id, "email": u.email, "name": u.name, "role": u.role, "status": u.status,
                 "created_at": u.created_at.date().isoformat(), "has_password": bool(u.password_hash),
                 "has_resume": bool(resumes.get(u.id)), "in_scope": scoped.get(u.id, 0), "marks": marks.get(u.id, 0)}
                for u in s.scalars(select(User).order_by(User.id))]


class UserPatch(BaseModel):
    role: Literal["admin", "member"] | None = None
    status: Literal["active", "disabled"] | None = None


@app.post("/api/users/{user_id}")
def patch_user(user_id: int, body: UserPatch):
    admin = require_admin()
    if user_id == admin.id and (body.role == "member" or body.status == "disabled"):
        raise HTTPException(400, "you cannot demote or disable your own account")
    with session() as s, s.begin():
        user = s.get(User, user_id)
        if user is None:
            raise HTTPException(404, "no such user")
        if body.role:
            user.role = body.role
        if body.status:
            user.status = body.status
            if body.status == "disabled":
                s.execute(delete(Session).where(Session.user_id == user_id))
        return {"id": user.id, "role": user.role, "status": user.status}


@app.get("/api/admin/overview")
def admin_overview():
    """What the admin tab shows: recent stage runs by day, the queue, counts of the shared pool."""
    require_admin()
    with session() as s:
        recent = s.execute(select(Run).order_by(Run.id.desc()).limit(60)).scalars().all()
        pool = {
            "postings": s.scalar(select(func.count()).select_from(RawPosting)),
            "postings_kept": s.scalar(select(func.count()).select_from(RawPosting).where(RawPosting.filter_status == "kept")),
            "jobs": s.scalar(select(func.count()).select_from(Job).where(Job.duplicate_of.is_(None))),
            "jobs_embedded": s.scalar(select(func.count()).select_from(Job).where(Job.duplicate_of.is_(None), Job.embedding.is_not(None))),
            "companies": s.scalar(select(func.count()).select_from(Company)),
            "users": s.scalar(select(func.count()).select_from(User).where(User.status == "active")),
        }
    return {
        "pool": pool,
        "stage_runs": [{"id": r.id, "date": str(r.run_date), "stage": r.stage, "user_id": r.user_id, "status": r.status,
                        "started_at": r.started_at.isoformat(timespec="seconds") if r.started_at else None,
                        "seconds": round((r.finished_at - r.started_at).total_seconds()) if r.started_at and r.finished_at else None,
                        "error": r.error} for r in recent],
        "queue": runs.active(),
    }


@app.get("/api/skills")
def skills_overview():
    """For onboarding: skills the resume mentions, the catalogue, and the profile's own lists."""
    from jobagent.skills import load_catalogue, my_skills
    from jobagent.users import get_profile
    uid = current_user()
    profile = get_profile(uid)["skills"]
    return {"detected": sorted(my_skills(uid)), "catalogue": list(load_catalogue()["skills"]),
            "have": profile.get("have") or [], "not_have": profile.get("not_have") or []}


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


def _posting_html(postings) -> str:
    """The first posting's own HTML, sanitised for display; empty when a source gives only text."""
    from jobagent.parse.html import sanitize_html
    for p in postings:
        if p.source in NORMALIZERS:
            html = NORMALIZERS[p.source](p.payload).description_html
            if html and html.strip():
                return sanitize_html(html)
    return ""


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
    sort: Literal["score", "newest", "match"] = "score", limit: int = 300, resume_id: int = 0,
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
        if resume_id:
            query = query.where(UserJob.applied_resume_id == resume_id)
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
            "applied_resume_id": u.applied_resume_id if u else None,
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
            "applied_resume_id": u.applied_resume_id if u else None,
            "notes": (u.notes if u else None) or "",
            "match_score": u.match_score if u else None, "match_explanation": u.match_explanation if u else None,
            "match_details": u.match_details if u else None,
            "scored_at": u.scored_at.date().isoformat() if u and u.scored_at else None,
            "sections": parsed.get("sections") or [], "description": j.description_text,
            "description_html": _posting_html(postings),
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
    applied_resume_id: int | None = None
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
                   B.c.id, B.c.title, B.c.locations, UB.c.fit_score, UB.c.user_label,
                   A.c.requisition_id, B.c.requisition_id)
            .join(A, A.c.id == PossibleDuplicate.job_a).join(B, B.c.id == PossibleDuplicate.job_b)
            .join(Company, Company.id == A.c.company_id)
            .outerjoin(UA, (UA.c.job_id == A.c.id) & (UA.c.user_id == uid))
            .outerjoin(UB, (UB.c.job_id == B.c.id) & (UB.c.user_id == uid))
            .where(PossibleDuplicate.status == "pending", A.c.duplicate_of.is_(None), B.c.duplicate_of.is_(None),
                   func.coalesce(UA.c.in_scope, False) | func.coalesce(UB.c.in_scope, False))
            .order_by(PossibleDuplicate.similarity.desc())
        ).all()
        return [{
            "id": r[0], "similarity": float(r[1]), "company": r[2],
            "a": {"id": r[3], "title": r[4], "location": _location_text(r[5]), "fit_score": r[6], "label": r[7], "req": r[13]},
            "b": {"id": r[8], "title": r[9], "location": _location_text(r[10]), "fit_score": r[11], "label": r[12], "req": r[14]},
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
        UA, UB = UserJob.__table__.alias("ua"), UserJob.__table__.alias("ub")
        pending = s.scalar(  # open pairs, not merged some other way since, with a job in your scope
            select(func.count()).select_from(PossibleDuplicate)
            .join(A, A.c.id == PossibleDuplicate.job_a).join(B, B.c.id == PossibleDuplicate.job_b)
            .outerjoin(UA, (UA.c.job_id == A.c.id) & (UA.c.user_id == uid))
            .outerjoin(UB, (UB.c.job_id == B.c.id) & (UB.c.user_id == uid))
            .where(PossibleDuplicate.status == "pending", A.c.duplicate_of.is_(None), B.c.duplicate_of.is_(None),
                   func.coalesce(UA.c.in_scope, False) | func.coalesce(UB.c.in_scope, False)))
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
    require_admin()
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
    command: Literal["pipeline", "run", "score"]
    request: str | None = None            # pipeline: the orchestrator's instruction
    tool: str | None = None               # run: the tool name
    args: dict[str, object] = {}          # run: its arguments


@app.post("/api/runs")
def start_run(body: RunBody):
    if body.command != "score":
        require_admin()  # the crawl and raw tools touch everyone's data
    if body.command == "pipeline":
        argv = ["pipeline", body.request or "Daily pull of all configured targets."]
        label = f"pipeline: {argv[1][:80]}"
    elif body.command == "score":
        argv = ["score", "--user", str(current_user())]
        label = "score my jobs (scope, fast score, judgement)"
    else:
        if body.tool not in TOOLS:
            raise HTTPException(400, "unknown tool")
        clean = {k: v for k, v in body.args.items() if v not in (None, "", []) and k != "user_id"}
        if "user_id" in TOOLS[body.tool].input_model.model_fields:
            clean["user_id"] = current_user()
        argv = ["run", body.tool, "--json", json.dumps(clean)]
        label = f"{body.tool} {json.dumps({k: v for k, v in clean.items() if k != 'user_id'})}"[:120]
    return runs.start(argv, label, current_user())


@app.get("/api/runs")
def list_runs():
    """Admins see every run; members only their own."""
    user = _request_user.get()
    mine = lambda r: user.role == "admin" or r.get("user_id") == user.id  # noqa: E731
    running = runs.running()
    return {"running": running and mine(running) and runs._public(running),
            "active": [r for r in runs.active() if mine(r)], "runs": [r for r in runs.load_runs() if mine(r)]}


def _visible_run(run_id: str) -> dict:
    run = runs.get(run_id)
    user = _request_user.get()
    if run is None or (user.role != "admin" and run.get("user_id") != user.id):
        raise HTTPException(404, "no such run")
    return run


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    return _visible_run(run_id)


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str):
    _visible_run(run_id)
    return {"stopped": runs.stop(run_id)}


# --- settings: profile sections and the shared config files ----------------------------------

@app.get("/api/config")
def list_config():
    items = configs.read_all(current_user())
    if _request_user.get().role != "admin":
        items = [c for c in items if c["kind"] == "profile"]
    return items


@app.get("/api/config/{name}")
def get_config(name: str):
    if name not in configs.PROFILE_SECTIONS:
        require_admin()
    try:
        return {**configs.read(name, current_user()), "backups": configs.backups(name)}
    except KeyError:
        raise HTTPException(404, "no such setting")


class ConfigBody(BaseModel):
    text: str | None = None            # raw YAML, saved as typed (files only)
    data: dict[str, object] | None = None   # form values


@app.put("/api/config/{name}")
def put_config(name: str, body: ConfigBody):
    if name not in configs.PROFILE_SECTIONS:
        require_admin()
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


# --- the current user's resume library -----------------------------------------------------

def _resume_rows(uid: int) -> list[dict]:
    with session() as s:
        used = dict(s.execute(select(UserJob.applied_resume_id, func.count()).where(
            UserJob.user_id == uid, UserJob.applied_resume_id.is_not(None)).group_by(UserJob.applied_resume_id)).all())
        rows = s.scalars(select(Resume).where(Resume.user_id == uid).order_by(Resume.active.desc(), Resume.id.desc())).all()
        return [{"id": r.id, "filename": r.filename or f"resume-{r.id}.md", "sha": r.sha[:12], "active": r.active,
                 "uploaded_at": r.uploaded_at.isoformat(timespec="seconds"), "chars": len(r.text),
                 "applications": used.get(r.id, 0)} for r in rows]


@app.get("/api/resumes")
def list_resumes():
    return _resume_rows(current_user())


@app.get("/api/resume")
def get_resume():
    """The active resume with its text (what the tracker scores against)."""
    from jobagent.users import active_resume
    with session() as s:
        r = active_resume(s, current_user())
        return {"id": r.id, "filename": r.filename, "sha": r.sha, "uploaded_at": r.uploaded_at.isoformat(timespec="seconds"),
                "chars": len(r.text), "text": r.text} if r else None


class ResumeBody(BaseModel):
    text: str
    filename: str | None = None
    activate: bool = True


@app.put("/api/resume")
def put_resume(body: ResumeBody):
    """Add a resume to the library (and, by default, score against it)."""
    from jobagent.tools.embeddings import UploadResumeInput, upload_resume
    name = (body.filename or "resume.md").strip()
    if not name.lower().endswith(".md"):
        name += ".md"
    result = upload_resume(UploadResumeInput(user_id=current_user(), text=body.text, filename=name, activate=body.activate))
    if result.error:
        raise HTTPException(400, result.error)
    return result


@app.get("/api/resumes/{resume_id}")
def get_resume_text(resume_id: int, download: bool = False):
    with session() as s:
        r = s.scalar(select(Resume).where(Resume.id == resume_id, Resume.user_id == current_user()))
        if r is None:
            raise HTTPException(404, "no such resume")
        if download:
            name = (r.filename or f"resume-{r.id}.md").replace('"', "")
            return Response(r.text, media_type="text/markdown; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})
        return {"id": r.id, "filename": r.filename, "active": r.active, "text": r.text,
                "uploaded_at": r.uploaded_at.isoformat(timespec="seconds")}


@app.post("/api/resumes/{resume_id}/activate")
def activate_resume_endpoint(resume_id: int):
    from jobagent.tools.embeddings import ResumeRefInput, activate_resume
    result = activate_resume(ResumeRefInput(user_id=current_user(), resume_id=resume_id))
    if result.error:
        raise HTTPException(400, result.error)
    return result


class ResumePatch(BaseModel):
    text: str | None = None
    filename: str | None = None


@app.patch("/api/resumes/{resume_id}")
def patch_resume(resume_id: int, body: ResumePatch):
    from jobagent.tools.embeddings import UpdateResumeInput, update_resume
    name = body.filename
    if name is not None:
        name = name.strip()
        if name and not name.lower().endswith(".md"):
            name += ".md"
    result = update_resume(UpdateResumeInput(user_id=current_user(), resume_id=resume_id, text=body.text, filename=name))
    if result.error:
        raise HTTPException(400, result.error)
    return result


@app.delete("/api/resumes/{resume_id}")
def delete_resume_endpoint(resume_id: int):
    from jobagent.tools.embeddings import ResumeRefInput, delete_resume
    result = delete_resume(ResumeRefInput(user_id=current_user(), resume_id=resume_id))
    if result.error:
        raise HTTPException(400, result.error)
    return result
