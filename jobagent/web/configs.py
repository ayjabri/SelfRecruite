"""Settings for the web page: the user's profile sections, and the shared files in config/.

Profile sections (targets, scope, skills) live in the database per user. The files are shared:
the crawl's company list, the skill catalogue, and the templates a new profile starts from.
File saves go through ruamel.yaml so the comments survive, after a backup to data/config_backups/.
"""
import io
from datetime import datetime
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from jobagent import groups
from jobagent.config import ROOT, get_settings
from jobagent.users import get_profile, profile_defaults, save_profile

BACKUPS = ROOT / "data" / "config_backups"
KEEP_BACKUPS = 20

# "score my jobs" re-scores whatever was scored under older settings, and anyone may run it
RESCORE = {"command": "score", "label": "score my jobs", "note": "Apply it to your jobs"}
PROFILE_SECTIONS = {
    "targets": {"title": "Scoring", "description": "Fit-score weights, title families, seniority, domain and the hard gates.",
                "after": RESCORE},
    "groups": {"title": "Company groups", "description": "Your own groups of companies, and which roles interest you in each group or company.",
               "after": RESCORE},
    "scope": {"title": "Scope", "description": "Which jobs count for you: title keywords and allowed states.",
              "after": {"tool": "filter_pending_postings", "args": {"recheck": True},
                        "note": "Re-check stored postings so newly wanted titles get parsed; your scope and scores update on the next fast score"}},
    "skills": {"title": "Skills", "description": "Skills you have beyond what your resume text mentions, and ones not to claim.",
               "after": None},
    "llm": {"title": "AI model", "description": "The local model (free) or Claude with your own API key, for keyword extraction and the AI features to come.",
            "after": None},
}
FILES = {
    "companies": {"title": "Companies", "description": "Which employers the shared crawl pulls from.", "after": None},
    "skill_catalogue": {"title": "Skill catalogue", "description": "The technical skills looked for in postings, with the phrases that mean them.",
                        "after": None},
    "filters_template": {"title": "Crawl coverage", "description": "Which countries and states the platform covers, and the title keywords a new profile starts from.",
                         "after": {"tool": "filter_pending_postings", "args": {"recheck": True},
                                   "note": "Re-check every stored posting against the new rules"}},
    "targets_template": {"title": "Scoring template", "description": "The scoring settings a new profile starts from.", "after": None},
}


def _path(name: str) -> Path:
    s = get_settings()
    known = {"companies": s.companies_file, "skill_catalogue": ROOT / "config" / "skills.yaml",
             "filters_template": s.filters_file, "targets_template": s.targets_file}
    if name not in known:
        raise KeyError(name)
    return known[name]


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 110
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _plain(obj):
    """ruamel containers -> plain dict/list for JSON."""
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def _llm_view(llm: dict) -> dict:
    """What the page may see of the llm section: never the key itself."""
    from jobagent.llm import provider_of
    return {"provider": provider_of(llm), "model": llm.get("model") or "",
            "has_key": bool(llm.get("api_key_enc")), "judge_budget_per_day": llm.get("judge_budget_per_day", 20)}


def _llm_save(user_id: int | None, data: dict) -> dict:
    from jobagent.llm import provider_of
    from jobagent.secrets import encrypt
    current = get_profile(user_id)["llm"] or {}
    new = {"provider": provider_of(data), "model": (data.get("model") or "").strip() or None,
           "judge_budget_per_day": current.get("judge_budget_per_day", 20)}
    if data.get("judge_budget_per_day") not in (None, ""):
        new["judge_budget_per_day"] = int(data["judge_budget_per_day"])
    key = (data.get("api_key") or "").strip()
    if data.get("clear_key"):
        new["api_key_enc"] = None
    elif key:
        from jobagent.llm import test_key
        check = test_key(new["provider"] if new["provider"] != "local" else "anthropic", key)
        if not check["ok"]:
            raise ValueError(f"key not saved: {check['detail']}")
        new["api_key_enc"] = encrypt(key)
    else:
        new["api_key_enc"] = current.get("api_key_enc")
    return new


def _groups_view(user_id: int | None) -> dict:
    """The Company groups page: the user's groups and interest statements, plus what the editor offers
    (their role families, every company in the database, and the template's suggestions)."""
    from sqlalchemy import select

    from jobagent.db import session
    from jobagent.models import Company
    targets, template = get_profile(user_id)["targets"], profile_defaults()["targets"]
    families = targets.get("title_families") or {}

    def known(interest: dict | None) -> dict:   # a family deleted on the Scoring page drops out of the statements
        interest = dict(interest or {})
        if interest:
            interest["families"] = [f for f in interest.get("families") or [] if f in families]
        return interest
    with session() as s:
        companies = list(s.scalars(select(Company.name).order_by(Company.name)))
    return {
        "data": {
            "company_groups": {g: {"companies": list((c or {}).get("companies") or []), "interest": known((c or {}).get("interest"))}
                               for g, c in (targets.get("company_groups") or {}).items()},
            "company_interest": {c: known(i) for c, i in (targets.get("company_interest") or {}).items()},
        },
        "families": {f: list((c or {}).get("keywords") or []) for f, c in families.items()},
        "companies": companies,
        "suggested_groups": template.get("company_groups") or {},
        "missing_families": {f: c for f, c in (template.get("title_families") or {}).items() if f not in families},
    }


def _groups_save(targets: dict, data: dict) -> dict:
    """Validated groups and interest statements merged into the targets. `add_families` copies role
    families from the template (how an existing profile gets the TPM family)."""
    families = dict(targets.get("title_families") or {})
    template = profile_defaults()["targets"].get("title_families") or {}
    for f in data.get("add_families") or []:
        if f in template and f not in families:
            families[f] = template[f]
    return {**targets, "title_families": families, **groups.validate(data, families)}


def read(name: str, user_id: int | None = None) -> dict:
    if name == "llm":
        return {"name": name, "kind": "profile", **PROFILE_SECTIONS[name], "data": _llm_view(get_profile(user_id)["llm"] or {})}
    if name == "groups":
        return {"name": name, "kind": "profile", **PROFILE_SECTIONS[name], **_groups_view(user_id)}
    if name in PROFILE_SECTIONS:
        data = get_profile(user_id)[name]
        if name == "targets":   # the groups have their own page
            data = {k: v for k, v in data.items() if k not in groups.KEYS}
        return {"name": name, "kind": "profile", **PROFILE_SECTIONS[name], "data": data}
    path = _path(name)
    text = path.read_text() if path.exists() else ""
    data = _yaml().load(text) if text.strip() else None
    return {"name": name, "kind": "file", "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            **FILES[name], "text": text, "data": _plain(data) if data is not None else {},
            "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds") if path.exists() else None}


def read_all(user_id: int | None = None) -> list[dict]:
    return [read(n, user_id) for n in (*PROFILE_SECTIONS, *FILES)]


def _merge(doc, data):
    """Put the form's values into the ruamel document in place, keeping comments on the keys that stay."""
    if not isinstance(doc, CommentedMap):
        return _to_ruamel(data)
    for key in list(doc.keys()):
        if key not in data:
            del doc[key]
    for key, value in data.items():
        if key in doc and isinstance(doc[key], CommentedMap) and isinstance(value, dict):
            _merge(doc[key], value)
        elif key in doc and isinstance(doc[key], CommentedSeq) and _is_flat_list(value):
            _merge_list(doc[key], value)
        else:
            doc[key] = _to_ruamel(value, flow=_is_flat_list(value))
    return doc


def _merge_list(seq: CommentedSeq, value: list) -> None:
    """Edit a flat list in place: drop removed items, append new ones. Items that stay keep their
    position, their comments and the list's block/flow style."""
    wanted = [str(v) if not isinstance(v, (int, float, bool)) else v for v in value]
    for i in range(len(seq) - 1, -1, -1):
        if seq[i] not in wanted:
            del seq[i]
    present = set(map(str, seq))
    for v in wanted:
        if str(v) not in present:
            seq.append(v)
            present.add(str(v))


def _is_flat_list(value) -> bool:
    return isinstance(value, list) and all(not isinstance(v, (dict, list)) for v in value)


def _to_ruamel(value, flow: bool = False):
    if isinstance(value, dict):
        m = CommentedMap()
        for k, v in value.items():
            m[k] = _to_ruamel(v, flow=_is_flat_list(v))
        if all(not isinstance(v, (dict, list)) for v in value.values()) and len(value) <= 4 and value:
            m.fa.set_flow_style()  # {company: X, site: Y} rows stay on one line
        return m
    if isinstance(value, list):
        s = CommentedSeq(_to_ruamel(v) for v in value)
        if flow:
            s.fa.set_flow_style()
        return s
    return value


def _backup(path: Path) -> None:
    if not path.exists():
        return
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (BACKUPS / f"{path.stem}.{stamp}{path.suffix}").write_text(path.read_text())
    old = sorted(BACKUPS.glob(f"{path.stem}.*{path.suffix}"))[:-KEEP_BACKUPS]
    for p in old:
        p.unlink()


def _clear_caches() -> None:
    from jobagent import skills, users
    from jobagent.config import load_targets
    from jobagent.tools import filtering
    for fn in (load_targets, filtering.load_filters, skills.load_catalogue, skills._resume_skills, users.profile_defaults):
        fn.cache_clear()


def save_text(name: str, text: str) -> dict:
    """Save the raw YAML as typed; it must parse."""
    path = _path(name)
    _yaml().load(text)  # raises on bad YAML
    _backup(path)
    path.write_text(text if text.endswith("\n") else text + "\n")
    _clear_caches()
    return read(name)


def save_data(name: str, data: dict, user_id: int | None = None) -> dict:
    """Save values from the form: into the user's profile, or merged into the file so its comments stay."""
    if name in ("groups", "targets"):
        current = get_profile(user_id)["targets"]
        if name == "groups":
            new = _groups_save(current, data)
        else:   # the Scoring page does not carry the groups: keep them
            new = {**{k: v for k, v in data.items() if k not in groups.KEYS}, **{k: current[k] for k in groups.KEYS if k in current}}
        save_profile(user_id, targets=new)
        _clear_caches()
        return read(name, user_id)
    if name in PROFILE_SECTIONS:
        save_profile(user_id, **{name: _llm_save(user_id, data) if name == "llm" else data})
        _clear_caches()
        return read(name, user_id)
    path = _path(name)
    y = _yaml()
    doc = y.load(path.read_text()) if path.exists() and path.read_text().strip() else CommentedMap()
    doc = _merge(doc if isinstance(doc, CommentedMap) else CommentedMap(), data)
    buf = io.StringIO()
    y.dump(doc, buf)
    _backup(path)
    path.write_text(buf.getvalue())
    _clear_caches()
    return read(name)


def backups(name: str) -> list[str]:
    if name in PROFILE_SECTIONS:
        return []
    path = _path(name)
    return [p.name for p in sorted(BACKUPS.glob(f"{path.stem}.*{path.suffix}"), reverse=True)]
