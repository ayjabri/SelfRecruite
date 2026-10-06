"""Users and profiles.

Until sign-in exists (Phase 3), every tool and the web page act as DEFAULT_USER_ID (1, the
admin created by the multi-user migration). Tools take `user_id` so an agent, the CLI or the
API can act for anyone.
"""
import os
from functools import lru_cache

import yaml
from sqlalchemy import select

from jobagent.config import ROOT, get_settings
from jobagent.db import session
from jobagent.models import Profile, Resume, User, UserJob

DEFAULT_USER_ID = int(os.environ.get("DEFAULT_USER_ID", "1"))


def resolve_user(user_id: int | None) -> int:
    return DEFAULT_USER_ID if user_id is None else user_id


@lru_cache
def profile_defaults() -> dict:
    """A new profile starts from the config/*.yaml templates."""
    s = get_settings()
    load = lambda p: (yaml.safe_load(p.read_text()) if p.exists() else None) or {}  # noqa: E731
    filters, targets = load(s.filters_file), load(s.targets_file)
    return {
        "targets": {k: v for k, v in targets.items() if k != "bank_title_companies"},
        "scope": {"title": filters.get("title") or {},
                  "location": {"allowed_states": (filters.get("location") or {}).get("allowed_states") or [],
                               "remote_ok": True}},
        # the catalogue is shared; which skills someone has is theirs alone, so a new profile claims none
        "skills": {"have": [], "not_have": []},
        "llm": {"judge_budget_per_day": 20},
    }


def get_profile(user_id: int | None = None) -> dict:
    """{'targets', 'scope', 'skills', 'llm'}; empty sections fall back to the templates."""
    uid = resolve_user(user_id)
    with session() as s:
        row = s.get(Profile, uid)
    defaults = profile_defaults()
    if row is None:
        return {k: dict(v) for k, v in defaults.items()}
    return {k: (getattr(row, k) or defaults[k]) for k in ("targets", "scope", "skills", "llm")}


def save_profile(user_id: int | None, **sections) -> dict:
    """Replace the given sections (targets, scope, skills, llm) of the user's profile."""
    uid = resolve_user(user_id)
    with session() as s, s.begin():
        row = s.get(Profile, uid)
        if row is None:
            row = Profile(user_id=uid)
            s.add(row)
        for key, value in sections.items():
            if key not in ("targets", "scope", "skills", "llm"):
                raise KeyError(key)
            setattr(row, key, value)
    return get_profile(uid)


def active_resume(s, user_id: int | None = None) -> Resume | None:
    uid = resolve_user(user_id)
    return s.scalars(select(Resume).where(Resume.user_id == uid, Resume.active).order_by(Resume.id.desc())).first()


def user_job(s, user_id: int, job_id: int) -> UserJob:
    """The user's row for a job, created on first touch."""
    row = s.get(UserJob, (user_id, job_id))
    if row is None:
        row = UserJob(user_id=user_id, job_id=job_id)
        s.add(row)
        s.flush()
    return row


def all_user_ids(s) -> list[int]:
    return list(s.scalars(select(User.id).where(User.status == "active").order_by(User.id)))
