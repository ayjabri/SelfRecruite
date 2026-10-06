"""De-duplication: merge copies of the same opening into one job record (no LLM).

Layers, cheapest first (BUILD_PLAN step 4):
  1. same source posting            -> the raw_postings unique key, at fetch time
  2. same company + requisition ID  -> linked at parse time
  3. identical description text     -> merged here
  4. near-identical                 -> merged here when title and embedding both agree;
                                       flagged for review when only the embedding does
Merging hides nothing you could apply to: every posting stays linked to the surviving job
with its own URL and location.
"""
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field
from rapidfuzz import fuzz
from sqlalchemy import func, select

from jobagent.db import session
from jobagent.models import Job, PossibleDuplicate, UserJob
from jobagent.tools.registry import tool_spec

log = logging.getLogger(__name__)

MERGE_SIM, MERGE_TITLE = 0.95, 90   # near-identical: merge
FLAG_SIM, FLAG_TITLE = 0.90, 85     # possibly the same: ask


_LEVEL_WORDS = {
    "intern", "junior", "jr", "associate", "analyst", "senior", "sr", "lead", "staff", "principal",
    "distinguished", "manager", "director", "head", "vp", "vice", "president", "chief", "executive",
    "managing", "i", "ii", "iii", "iv", "v", "1", "2", "3", "4",
}


def _level_words(title_norm: str) -> frozenset[str]:
    """The words in a title that mark its level; "Senior X" and "X" are different openings."""
    return frozenset(w for w in re.findall(r"[a-z0-9]+", title_norm) if w in _LEVEL_WORDS)


class _Node:
    def __init__(self, row):
        self.id, self.title_norm, self.text = row.id, row.title_norm, row.description_text
        self.embedding = row.embedding
        self.locations = row.locations or []
        self.parsed = row.parsed
        self.in_scope = row.in_scope


def _find(parent, x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _merge_into(s, canonical: Job, dup: Job) -> None:
    """Point `dup` at `canonical`, carrying its locations and every user's marks across."""
    seen = {tuple(sorted((l or {}).items())) for l in canonical.locations or []}
    extra = [l for l in dup.locations or [] if tuple(sorted((l or {}).items())) not in seen]
    if extra:
        canonical.locations = [*(canonical.locations or []), *extra]
        if canonical.parsed:
            canonical.parsed = {**canonical.parsed, "locations": canonical.locations}
    for theirs in s.scalars(select(UserJob).where(UserJob.job_id == dup.id)):
        mine = s.get(UserJob, (theirs.user_id, canonical.id))
        if mine is None:
            mine = UserJob(user_id=theirs.user_id, job_id=canonical.id, in_scope=theirs.in_scope)
            s.add(mine)
        if theirs.user_label and not mine.user_label:
            mine.user_label, mine.user_labeled_at = theirs.user_label, theirs.user_labeled_at
        mine.favorite = mine.favorite or theirs.favorite
        mine.applied_at = mine.applied_at or theirs.applied_at
        if theirs.notes and not mine.notes:
            mine.notes = theirs.notes
        if extra:
            mine.fit_score = None  # new locations can change the location gate; re-score
    dup.duplicate_of, dup.status = canonical.id, "duplicate"


def merge_jobs(s, job_ids: list[int]) -> int:
    """Merge the given jobs into the earliest of them. Returns the canonical id."""
    jobs = sorted((s.get(Job, i) for i in job_ids), key=lambda j: j.id)
    canonical = next((j for j in jobs if j.duplicate_of is None), jobs[0])
    if canonical.duplicate_of is not None:
        canonical = s.get(Job, canonical.duplicate_of)
    for j in jobs:
        if j.id != canonical.id and j.duplicate_of is None:
            _merge_into(s, canonical, j)
    return canonical.id


class DedupInput(BaseModel):
    dry_run: bool = Field(False, description="Report what would be merged or flagged without changing anything")


class DedupResult(BaseModel):
    jobs_checked: int = 0
    merged_identical_text: int = 0
    merged_near_identical: int = 0
    flagged_for_review: int = 0
    pending_review: int = 0        # possible duplicates still waiting for a decision, in-scope only
    canonical_jobs: int = 0        # jobs left after merging (the ones you see)
    sample_merges: list[str] = []  # a few in-scope merges, "title A <> title B (reason)"
    dry_run: bool = False


@tool_spec(
    name="dedup_jobs",
    description=(
        "Merge duplicate job records (same opening posted several times) into one, using identical "
        "text, then title similarity plus embedding similarity, within each company. Pairs that look "
        "alike but not alike enough are recorded as possible duplicates for the user to decide. "
        "No LLM. Safe to rerun."
    ),
)
def dedup_jobs(args: DedupInput) -> DedupResult:
    out = DedupResult(dry_run=args.dry_run)
    # a pair is only worth a person's time if someone has one of its jobs in scope
    kept = select(UserJob.job_id).where(UserJob.job_id == Job.id, UserJob.in_scope).exists()
    with session() as s, s.begin():
        rows = s.execute(
            select(Job.id, Job.company_id, Job.title_norm, Job.description_text, Job.embedding,
                   Job.locations, Job.parsed, kept.label("in_scope"))
            .where(Job.duplicate_of.is_(None)).order_by(Job.id)
        ).all()
        decided = {
            (a, b) for a, b in s.execute(select(PossibleDuplicate.job_a, PossibleDuplicate.job_b))
        }
        out.jobs_checked = len(rows)
        by_company: dict[int, list[_Node]] = defaultdict(list)
        for r in rows:
            by_company[r.company_id].append(_Node(r))

        parent = {r.id: r.id for r in rows}
        reasons: dict[tuple[int, int], str] = {}
        flags: list[tuple[int, int, float, int]] = []
        for nodes in by_company.values():
            # layer 3: identical text
            by_text: dict[str, int] = {}
            for n in nodes:
                first = by_text.setdefault(n.text or f"<{n.id}>", n.id)
                if first != n.id:
                    parent[_find(parent, n.id)] = _find(parent, first)
                    reasons[(first, n.id)] = "identical"
            # layer 4: near-identical, by embedding and title
            with_vec = [n for n in nodes if n.embedding is not None]
            if len(with_vec) < 2:
                continue
            E = np.array([n.embedding for n in with_vec], dtype=np.float32)
            E /= np.linalg.norm(E, axis=1, keepdims=True)
            sims = E @ E.T
            for i in range(len(with_vec)):
                for k in range(i + 1, len(with_vec)):
                    sim = float(sims[i, k])
                    if sim < FLAG_SIM:
                        continue
                    a, b = with_vec[i], with_vec[k]
                    if _find(parent, a.id) == _find(parent, b.id):
                        continue
                    sort_ratio = fuzz.token_sort_ratio(a.title_norm, b.title_norm)
                    same_level = _level_words(a.title_norm) == _level_words(b.title_norm)
                    if sim >= MERGE_SIM and sort_ratio >= MERGE_TITLE and same_level:
                        parent[_find(parent, b.id)] = _find(parent, a.id)
                        reasons[(a.id, b.id)] = "near"
                    elif fuzz.token_set_ratio(a.title_norm, b.title_norm) >= FLAG_TITLE and (
                        a.in_scope or b.in_scope
                    ) and (a.id, b.id) not in decided:
                        flags.append((a.id, b.id, sim, int(sort_ratio)))

        groups: dict[int, list[int]] = defaultdict(list)
        for job_id in parent:
            groups[_find(parent, job_id)].append(job_id)
        merges = [sorted(ids) for ids in groups.values() if len(ids) > 1]
        titles = {n.id: (n.title_norm, n.in_scope) for nodes in by_company.values() for n in nodes}
        for (a, b), why in reasons.items():
            if len(out.sample_merges) < 12 and (titles[a][1] or titles[b][1]):
                out.sample_merges.append(f"{titles[a][0]} <> {titles[b][0]} ({why})")
        out.merged_identical_text = sum(1 for v in reasons.values() if v == "identical")
        out.merged_near_identical = sum(1 for v in reasons.values() if v == "near")
        out.flagged_for_review = len(flags)
        out.canonical_jobs = len(rows) - sum(len(g) - 1 for g in merges)
        if args.dry_run:
            s.rollback()
            return out
        for ids in merges:
            merge_jobs(s, ids)
        merged_ids = {i for g in merges for i in g[1:]}
        s.add_all(
            PossibleDuplicate(job_a=a, job_b=b, similarity=round(sim, 3), title_ratio=ratio)
            for a, b, sim, ratio in flags if a not in merged_ids and b not in merged_ids
        )
        s.flush()
        out.pending_review = s.scalar(
            select(func.count()).select_from(PossibleDuplicate).where(PossibleDuplicate.status == "pending")
        )
    log.info("dedup: %d merged (%d identical, %d near), %d flagged", sum(len(g) - 1 for g in merges),
             out.merged_identical_text, out.merged_near_identical, out.flagged_for_review)
    return out


class DecideDuplicateInput(BaseModel):
    pair_id: int = Field(description="possible_duplicates.id")
    decision: Literal["same", "different"] = Field(description="'same' merges the pair; 'different' keeps both")


class DecideDuplicateResult(BaseModel):
    pair_id: int
    decision: str | None = None
    canonical_job_id: int | None = None
    error: str | None = None


@tool_spec(
    name="decide_duplicate",
    description="Resolve a possible-duplicate pair: merge the two jobs, or record that they are different.",
)
def decide_duplicate(args: DecideDuplicateInput) -> DecideDuplicateResult:
    with session() as s, s.begin():
        pair = s.get(PossibleDuplicate, args.pair_id)
        if pair is None:
            return DecideDuplicateResult(pair_id=args.pair_id, error="no such pair")
        pair.status, pair.decided_at = args.decision, datetime.now(timezone.utc)
        canonical = merge_jobs(s, [pair.job_a, pair.job_b]) if args.decision == "same" else None
        return DecideDuplicateResult(pair_id=pair.id, decision=args.decision, canonical_job_id=canonical)
