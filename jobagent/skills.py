"""Technical skills: find the ones a posting asks for, and split them into have / missing.

Rule-based (config/skills.yaml), so it runs on every job for free. `my_skills()` is what the
resume text mentions plus the `have` list in the config, minus `not_have`.
"""
import re
from functools import lru_cache
from pathlib import Path

import yaml

from jobagent.config import ROOT, get_settings

SKILLS_FILE = ROOT / "config" / "skills.yaml"


def _pattern(phrase: str) -> str:
    p = phrase.strip()
    if not p:
        return ""
    body = re.escape(p)
    # whole-word, but let phrases that start/end with a symbol (c++, .net, "(r)") match as they are
    start = r"(?<![a-z0-9])" if p[0].isalnum() else ""
    end = r"(?![a-z0-9+#])" if p[-1].isalnum() else ""
    return start + body + end


@lru_cache
def load_catalogue() -> dict:
    data = yaml.safe_load(SKILLS_FILE.read_text()) if SKILLS_FILE.exists() else None
    data = data or {}
    compiled = {}
    for name, phrases in (data.get("skills") or {}).items():
        pats = [_pattern(str(ph)) for ph in (phrases or [])]
        pats = [p for p in pats if p]
        if pats:
            compiled[name] = re.compile("|".join(pats), re.IGNORECASE)
    return {"skills": compiled, "have": set(data.get("have") or []), "not_have": set(data.get("not_have") or [])}


def extract_skills(text: str) -> list[str]:
    """Canonical skill names mentioned in `text`, in catalogue order."""
    if not text:
        return []
    cat = load_catalogue()["skills"]
    return [name for name, rx in cat.items() if rx.search(text)]


@lru_cache
def my_skills() -> frozenset[str]:
    cat = load_catalogue()
    path: Path = get_settings().golden_source_file
    found = set(extract_skills(path.read_text())) if path.exists() else set()
    return frozenset((found | cat["have"]) - cat["not_have"])


def skill_gap(text: str) -> dict[str, list[str]]:
    """{'matching': [...], 'missing': [...]} for a posting's text."""
    mine = my_skills()
    wanted = extract_skills(text)
    return {"matching": [s for s in wanted if s in mine], "missing": [s for s in wanted if s not in mine]}
