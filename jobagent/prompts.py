"""The AI skills: system prompts shared by every user, editable by admins, versioned.

A prompt is seeded from config/prompts/<name>.md the first time it is read and lives in the
`prompts` table afterwards; every save keeps the previous text in `prompt_versions`.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from jobagent.config import ROOT
from jobagent.db import session
from jobagent.models import Prompt, PromptVersion

PROMPTS_DIR = ROOT / "config" / "prompts"


def _seed_text(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise KeyError(f"no prompt named {name!r} (no row and no {path})")
    return path.read_text()


def get_prompt(name: str) -> tuple[str, int]:
    """(text, version). Seeds from the file on first use."""
    with session() as s, s.begin():
        row = s.get(Prompt, name)
        if row is None:
            row = Prompt(name=name, text=_seed_text(name), version=1)
            s.add(row)
            s.flush()
        return row.text, row.version


def list_prompts() -> list[dict]:
    names = {p.stem for p in PROMPTS_DIR.glob("*.md")} if PROMPTS_DIR.exists() else set()
    with session() as s:
        rows = {r.name: r for r in s.scalars(select(Prompt))}
        names |= set(rows)
        out = []
        for name in sorted(names):
            r = rows.get(name)
            out.append({"name": name, "version": r.version if r else 0,
                        "updated_at": r.updated_at.isoformat(timespec="seconds") if r and r.updated_at else None,
                        "chars": len(r.text) if r else len(_seed_text(name))})
        return out


def save_prompt(name: str, text: str, user_id: int | None) -> tuple[str, int]:
    text = text.rstrip() + "\n"
    if not text.strip():
        raise ValueError("the prompt is empty")
    with session() as s, s.begin():
        row = s.get(Prompt, name)
        if row is None:
            row = Prompt(name=name, text=_seed_text(name), version=1)
            s.add(row)
            s.flush()
        if row.text == text:
            return row.text, row.version
        s.add(PromptVersion(name=name, version=row.version, text=row.text, updated_by=row.updated_by, updated_at=row.updated_at))
        row.text, row.version = text, row.version + 1
        row.updated_by, row.updated_at = user_id, datetime.now(timezone.utc)
        return row.text, row.version


def history(name: str) -> list[dict]:
    with session() as s:
        rows = s.scalars(select(PromptVersion).where(PromptVersion.name == name).order_by(PromptVersion.version.desc())).all()
        return [{"version": r.version, "updated_at": r.updated_at.isoformat(timespec="seconds") if r.updated_at else None,
                 "updated_by": r.updated_by, "text": r.text} for r in rows]


def restore_prompt(name: str, version: int, user_id: int | None) -> tuple[str, int]:
    with session() as s:
        old = s.scalar(select(PromptVersion).where(PromptVersion.name == name, PromptVersion.version == version))
        if old is None:
            raise KeyError(f"no version {version} of {name}")
        text = old.text
    return save_prompt(name, text, user_id)
