"""Company groups: a user's own grouping of employers and what interests them in each.

Lives in the profile's targets:

    company_groups:   {Tech: {companies: [NVIDIA, Anthropic], interest: {families: [G_tpm], keywords: [], otherwise: cap}}}
    company_interest: {Citi: {families: [E_treasury_alm], keywords: [alm], otherwise: cap}}

An interest statement says which jobs the user would take there: a job qualifies when its title
matches any listed role family, or its title or requirement bullets contain any listed keyword.
Every other job at those companies is capped (score at most 30) or hidden (out of scope). An
empty statement restricts nothing. A company's own statement replaces its group's.
"""
import hashlib
import json
import re
from dataclasses import dataclass


KEYS = ("company_groups", "company_interest")
NO_SEGMENT = "none"


def norm(name: str) -> str:
    """Company names compare without case, spaces or punctuation: 'U.S. Bank' = 'US Bank', 'Master Card' = 'Mastercard'."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def phrase_regex(phrases) -> re.Pattern | None:
    """Whole-word, case-insensitive match for any of the phrases (as jobagent.tools.geo.phrase_regex;
    repeated here so this module imports nothing from the tools package)."""
    phrases = [p.strip() for p in phrases if p and p.strip()]
    if not phrases:
        return None
    alt = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alt})(?![A-Za-z0-9])", re.I)


@dataclass(frozen=True)
class Verdict:
    segment: str                 # what the learned model sees: the company if it has its own statement, else its group
    group: str | None
    rule_from: str | None        # "company" | "group" | None: where the statement that applied came from
    qualifies: bool = True
    action: str | None = None    # "cap" | "hide" when the job does not qualify
    reason: str | None = None


class _Rule:
    def __init__(self, interest: dict | None, families: dict, where: str, label: str):
        interest = interest or {}
        self.where, self.label = where, label
        self.otherwise = interest.get("otherwise") if interest.get("otherwise") in ("cap", "hide") else "cap"
        names = [f for f in interest.get("families") or [] if f in (families or {})]
        self.family_rx = [phrase_regex((families[f] or {}).get("keywords") or []) for f in names]
        self.family_rx = [rx for rx in self.family_rx if rx is not None]
        self.keyword_rx = phrase_regex(interest.get("keywords") or [])
        self.active = bool(self.family_rx or self.keyword_rx)

    def qualifies(self, title: str, bullets) -> bool:
        if any(rx.search(title) for rx in self.family_rx):
            return True
        return bool(self.keyword_rx and (self.keyword_rx.search(title) or any(self.keyword_rx.search(b) for b in bullets or [])))


class Segments:
    """Resolves a company to its segment and applies the user's interest statements."""

    def __init__(self, targets: dict | None):
        targets = targets or {}
        families = targets.get("title_families") or {}
        self._group_of: dict[str, str] = {}
        self._group_rule: dict[str, _Rule] = {}
        for group, cfg in (targets.get("company_groups") or {}).items():
            cfg = cfg or {}
            self._group_rule[group] = _Rule(cfg.get("interest"), families, "group", f"{group} companies")
            for company in cfg.get("companies") or []:
                self._group_of.setdefault(norm(company), group)
        self._company: dict[str, tuple[str, _Rule]] = {
            norm(company): (company, _Rule(interest, families, "company", company))
            for company, interest in (targets.get("company_interest") or {}).items()
        }
        self.has_rules = any(r.active for r in self._group_rule.values()) or any(r.active for _, r in self._company.values())
        self.needs_bullets = any(r.keyword_rx for r in self._group_rule.values()) or any(r.keyword_rx for _, r in self._company.values())

    def group(self, company: str) -> str | None:
        return self._group_of.get(norm(company))

    def segment(self, company: str) -> str:
        own = self._company.get(norm(company))
        return own[0] if own else (self.group(company) or NO_SEGMENT)

    def check(self, company: str, title: str, bullets=None) -> Verdict:
        group, own = self.group(company), self._company.get(norm(company))
        rule = own[1] if own else self._group_rule.get(group) if group else None
        segment = own[0] if own else (group or NO_SEGMENT)
        if rule is None or not rule.active:
            return Verdict(segment, group, None)
        if rule.qualifies(title or "", bullets):
            return Verdict(segment, group, rule.where)
        at = f"at {rule.label}"
        return Verdict(segment, group, rule.where, False, rule.otherwise,
                       f"not a role you target {at}" if rule.otherwise == "cap" else f"hidden: not a role you target {at}")


def title_family(title: str, families: dict | None) -> tuple[float, str | None]:
    """The highest-weighted role family whose keywords appear in the title: (weight, name), or (0, None)."""
    best, name = 0.0, None
    for family, cfg in (families or {}).items():
        rx = phrase_regex((cfg or {}).get("keywords") or [])
        if rx and rx.search(title) and float((cfg or {}).get("weight", 1.0)) > best:
            best, name = float(cfg.get("weight", 1.0)), family
    return best, name


def _clean_interest(raw, families: dict, where: str) -> dict:
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: the interest must be a mapping")
    fams = [str(f) for f in raw.get("families") or []]
    unknown = [f for f in fams if f not in families]
    if unknown:
        raise ValueError(f"{where}: unknown role families {', '.join(unknown)} (see Scoring → Title families)")
    keywords = [str(k).strip() for k in raw.get("keywords") or [] if str(k).strip()]
    otherwise = raw.get("otherwise") or "cap"
    if otherwise not in ("cap", "hide"):
        raise ValueError(f"{where}: 'otherwise' must be cap or hide")
    if not fams and not keywords:
        return {}
    return {"families": fams, "keywords": keywords, "otherwise": otherwise}


def validate(data: dict, families: dict) -> dict:
    """Check and tidy what the Company groups page sends. Raises ValueError with a message for the page."""
    groups, seen = {}, {}
    for name, cfg in (data.get("company_groups") or {}).items():
        name = str(name).strip()
        if not name:
            raise ValueError("a group needs a name")
        if name in groups:
            raise ValueError(f"two groups are called {name}")
        cfg = cfg or {}
        companies = []
        for company in cfg.get("companies") or []:
            company = str(company).strip()
            if not company:
                continue
            if norm(company) in seen and seen[norm(company)] != name:
                raise ValueError(f"{company} is in two groups ({seen[norm(company)]} and {name}); a company belongs to one")
            if norm(company) not in seen:
                seen[norm(company)] = name
                companies.append(company)
        groups[name] = {"companies": companies, "interest": _clean_interest(cfg.get("interest"), families, f"group {name}")}
    own = {}
    for company, interest in (data.get("company_interest") or {}).items():
        company = str(company).strip()
        cleaned = _clean_interest(interest, families, company)
        if company and cleaned:
            own[company] = cleaned
    return {"company_groups": groups, "company_interest": own}


def targets_sha(targets: dict | None) -> str:
    """Changes whenever anything in the scoring settings changes: scores computed under another value are stale."""
    return hashlib.sha256(json.dumps(targets or {}, sort_keys=True, default=str).encode()).hexdigest()[:12]
