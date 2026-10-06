"""Pre-filter tools: mark stored postings as kept or filtered before any LLM call."""
import re
from collections import Counter
from functools import lru_cache

import yaml
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from jobagent.config import get_settings
from jobagent.db import session
from jobagent.models import RawPosting
from jobagent.tools.common import NORMALIZERS, NormalizedPosting
from jobagent.tools.geo import classify_location, phrase_regex
from jobagent.tools.registry import tool_spec

KEPT, FILTERED = "kept", "filtered"
SAMPLE = 8


def _profile_includes() -> list[str]:
    """Title keywords every active user asked for, so the crawl parses what any of them wants."""
    try:
        from jobagent.models import Profile
        with session() as s:
            scopes = s.scalars(select(Profile.scope)).all()
    except Exception:  # noqa: BLE001 - before the migration ran, or no database: templates only
        return []
    return [k for scope in scopes for k in ((scope or {}).get("title") or {}).get("include") or []]


@lru_cache
def load_filters() -> dict:
    path = get_settings().filters_file
    cfg = (yaml.safe_load(path.read_text()) if path.exists() else None) or {}
    title, location = cfg.get("title") or {}, cfg.get("location") or {}
    return {
        "include": phrase_regex([*(title.get("include") or []), *_profile_includes()]),
        "exclude": phrase_regex(title.get("exclude") or []),
        "us_only": bool(location.get("us_only", True)),
        "extra_us": tuple(location.get("extra_us") or ()),
        "extra_non_us": tuple(location.get("extra_non_us") or ()),
        "allowed_states": tuple(str(s).upper() for s in location.get("allowed_states") or ()),
    }


_REMOTE = re.compile(r"\bremote\b", re.I)


def location_states(location_text: str) -> set[str]:
    """US state codes a posting's location text names (empty when none can be read)."""
    from jobagent.parse.core import parse_locations  # lazy: parse.core imports this package
    return {loc.state for loc in parse_locations(location_text) if loc.state}


def evaluate(posting: NormalizedPosting, cfg: dict | None = None) -> tuple[bool, str]:
    """(keep?, reason). Reasons start with a category so they can be counted."""
    cfg = cfg or load_filters()
    if cfg["exclude"] and (m := cfg["exclude"].search(posting.title)):
        return False, f"excluded title keyword: {m.group(0).lower()}"
    if cfg["include"] and not cfg["include"].search(posting.title):
        return False, "no target title keyword"
    if cfg["us_only"]:
        where, place = classify_location(posting.location_text, cfg["extra_us"], cfg["extra_non_us"])
        if where == "non_us":
            return False, f"outside US: {place}"
    allowed = set(cfg.get("allowed_states") or ())
    if allowed and not _REMOTE.search(posting.location_text or ""):
        states = location_states(posting.location_text)
        if states and not (states & allowed):
            return False, f"outside {'/'.join(sorted(allowed))}: {', '.join(sorted(states))}"
    return True, "matched"


class FilterInput(BaseModel):
    recheck: bool = Field(False, description=(
        "Re-evaluate every stored posting, including ones checked earlier. Use after "
        "config/filters.yaml changes. Jobs already created are kept but fall out of scope."))
    dry_run: bool = Field(False, description="Report what would happen without saving anything")
    states: list[str] | None = Field(None, description=(
        "Keep only postings in these US states (codes, e.g. ['NY', 'NJ']) for this run, overriding "
        "config/filters.yaml. Remote postings and unreadable locations are kept. [] disables the gate."))


class FilterResult(BaseModel):
    checked: int = 0
    kept: int = 0
    filtered: int = 0
    filtered_by_reason: dict[str, int] = {}
    sample_kept: list[str] = []
    sample_filtered: list[str] = []
    dry_run: bool = False


@tool_spec(
    name="filter_pending_postings",
    description=(
        "Apply the title and location pre-filter (config/filters.yaml) to stored postings that have not "
        "been checked yet. Kept postings go on to extraction; filtered ones stay stored but are skipped. "
        "No LLM is used. Returns counts, the reasons for filtering, and a few sample titles."
    ),
)
def filter_pending_postings(args: FilterInput) -> FilterResult:
    load_filters.cache_clear()  # profiles may have changed since this process started
    cfg = load_filters()
    if args.states is not None:
        cfg = {**cfg, "allowed_states": tuple(s.upper() for s in args.states)}
    out = FilterResult(dry_run=args.dry_run)
    reasons: Counter[str] = Counter()
    decisions: dict[int, tuple[str, str]] = {}
    with session() as s:
        query = select(RawPosting.id, RawPosting.source, RawPosting.payload)
        if not args.recheck:
            query = query.where(RawPosting.filter_status.is_(None))
        for raw_id, source, payload in s.execute(query.order_by(RawPosting.id)):
            normalizer = NORMALIZERS.get(source)
            if normalizer is None:
                continue
            posting = normalizer(payload)
            keep, reason = evaluate(posting, cfg)
            decisions[raw_id] = (KEPT if keep else FILTERED, reason)
            label = f"{posting.title} [{posting.location_text or 'no location'}]"
            if keep:
                out.kept += 1
                if len(out.sample_kept) < SAMPLE:
                    out.sample_kept.append(label)
            else:
                out.filtered += 1
                reasons[reason.split(":")[0]] += 1
                if len(out.sample_filtered) < SAMPLE:
                    out.sample_filtered.append(f"{label} - {reason}")
    out.checked = len(decisions)
    out.filtered_by_reason = dict(reasons.most_common())
    if decisions and not args.dry_run:
        with session() as s, s.begin():
            s.execute(update(RawPosting), [
                {"id": i, "filter_status": status, "filter_reason": reason}
                for i, (status, reason) in decisions.items()
            ])
    return out
