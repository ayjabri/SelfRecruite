"""Read and edit the files in config/ from the web page.

Saves go through ruamel.yaml so the comments in the files survive; each save first copies
the old file to data/config_backups/. Caches in this process are cleared after a save
(tools started from the Run tab are separate processes and always read the files fresh).
"""
import io
from datetime import datetime
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from jobagent.config import ROOT, get_settings

BACKUPS = ROOT / "data" / "config_backups"
KEEP_BACKUPS = 20

# name -> (path, title, what to run after a change)
FILES = {
    "filters": {"title": "Pre-filter", "description": "Which postings are kept before parsing: title keywords and location.",
                "after": {"tool": "filter_pending_postings", "args": {"recheck": True},
                          "note": "Re-check every stored posting against the new rules"}},
    "targets": {"title": "Scoring", "description": "Fit-score weights, title families, seniority, domain and the hard gates.",
                "after": {"tool": "score_jobs_fast", "args": {"rescore": True},
                          "note": "Re-score every job (then retrain the preference model)"}},
    "companies": {"title": "Companies", "description": "Which employers each source pulls from.", "after": None},
    "skills": {"title": "Skills", "description": "The technical-skill catalogue and which skills you have.", "after": None},
}


def _path(name: str) -> Path:
    s = get_settings()
    known = {"filters": s.filters_file, "targets": s.targets_file, "companies": s.companies_file,
             "skills": ROOT / "config" / "skills.yaml"}
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


def read(name: str) -> dict:
    path = _path(name)
    text = path.read_text() if path.exists() else ""
    data = _yaml().load(text) if text.strip() else None
    return {"name": name, "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path), **FILES[name], "text": text,
            "data": _plain(data) if data is not None else {},
            "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds") if path.exists() else None}


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
    from jobagent import skills
    from jobagent.config import load_targets
    from jobagent.tools import filtering
    for fn in (load_targets, filtering.load_filters, skills.load_catalogue, skills.my_skills):
        fn.cache_clear()


def save_text(name: str, text: str) -> dict:
    """Save the raw YAML as typed; it must parse."""
    path = _path(name)
    _yaml().load(text)  # raises on bad YAML
    _backup(path)
    path.write_text(text if text.endswith("\n") else text + "\n")
    _clear_caches()
    return read(name)


def save_data(name: str, data: dict) -> dict:
    """Save values from the form, merged into the existing file so its comments stay."""
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
    path = _path(name)
    return [p.name for p in sorted(BACKUPS.glob(f"{path.stem}.*{path.suffix}"), reverse=True)]
