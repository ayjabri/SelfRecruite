"""What the keywords agent is given besides the posting: terms similar roles ask for.

No model is trained here. The job embeddings already stored on every job (title + requirement
bullets) find the nearest historical postings; the terms are then counted two ways over that
set: hits from the skill catalogue, and phrases that are distinctive for the set against the
whole pool (TF-IDF over 1-3-grams). Everything is recomputed per request; the TF-IDF matrix is
cached per process and refreshed when the pool grows.
"""
import re
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sqlalchemy import func, select

from jobagent.db import session
from jobagent.models import Company, Job
from jobagent.skills import extract_skills
from jobagent.tools.scoring import title_family
from jobagent.users import get_profile

N_SIMILAR = 25
N_TERMS = 30
CANDIDATES = 120            # nearest by embedding before the family filter
MIN_DOC_FRACTION = 0.2      # a phrase must appear in at least this share of the similar set
MIN_DISTINCTIVENESS = 2.0   # mean TF-IDF in the similar set / mean over the whole pool
_GENERIC = {
    "experience", "years", "team", "work", "working", "ability", "strong", "skills", "role", "job", "business",
    "including", "related", "required", "preferred", "degree", "knowledge", "environment", "opportunity",
    "equivalent", "candidate", "candidates", "position", "responsibilities", "qualifications", "company",
    "employees", "benefits", "salary", "range", "status", "law", "applicants", "employment", "equal",
    # fragments and filler that TF-IDF n-grams pick up
    "ll", "re", "ve", "usd", "level", "clear", "senior", "lead", "manager", "teams", "guide", "sites", "deep",
    "requirements", "insights", "advancing", "balancing", "competing", "join", "help", "across", "new", "best",
}


@dataclass
class SimilarTerm:
    term: str
    kind: str          # skill | phrase
    count: int         # similar postings that mention it
    of: int            # size of the similar set

    def as_dict(self) -> dict:
        return {"term": self.term, "kind": self.kind, "count": self.count, "of": self.of}


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


@lru_cache(maxsize=2)
def _pool(stamp: int) -> tuple[list[int], TfidfVectorizer, object, np.ndarray]:
    """TF-IDF over every canonical job's text; `stamp` (job count + max id) keys the cache."""
    with session() as s:
        rows = s.execute(select(Job.id, Job.description_text).where(Job.duplicate_of.is_(None), Job.description_text.is_not(None))).all()
    ids = [r.id for r in rows]
    vec = TfidfVectorizer(ngram_range=(1, 3), min_df=3, max_df=0.6, sublinear_tf=True, stop_words="english",
                          token_pattern=r"(?u)\b[A-Za-z][A-Za-z+#./-]{1,}\b")
    matrix = vec.fit_transform([_clean(r.description_text) for r in rows])
    return ids, vec, matrix, np.asarray(matrix.mean(axis=0)).ravel()


def _pool_stamp() -> int:
    with session() as s:
        n, mx = s.execute(select(func.count(), func.max(Job.id)).where(Job.duplicate_of.is_(None))).one()
    return int(n or 0) * 100000 + int(mx or 0)


def similar_jobs(job_id: int, user_id: int | None = None, n: int = N_SIMILAR) -> list[dict]:
    """The n nearest canonical jobs by embedding, preferring the same title family as the job."""
    families = (get_profile(user_id)["targets"] or {}).get("title_families") or {}
    with session() as s:
        me = s.execute(select(Job.title, Job.embedding).where(Job.id == job_id)).first()
        if me is None or me.embedding is None:
            return []
        _, family = title_family(me.title, families)
        rows = s.execute(
            select(Job.id, Job.title, Company.name, Job.embedding.cosine_distance(me.embedding).label("d"))
            .join(Company, Company.id == Job.company_id)
            .where(Job.id != job_id, Job.duplicate_of.is_(None), Job.embedding.is_not(None))
            .order_by("d").limit(CANDIDATES)
        ).all()
    same = [r for r in rows if family and title_family(r.title, families)[1] == family]
    chosen = (same if len(same) >= max(5, n // 3) else rows)[:n]
    return [{"id": r.id, "title": r.title, "company": r.name, "similarity": round(1 - float(r.d), 3), "family": family} for r in chosen]


def similar_terms(job_id: int, user_id: int | None = None, n_jobs: int = N_SIMILAR, n_terms: int = N_TERMS) -> dict:
    """{'similar': [...jobs], 'terms': [...SimilarTerm.as_dict()]} for the keywords prompt."""
    sims = similar_jobs(job_id, user_id, n_jobs)
    if not sims:
        return {"similar": [], "terms": []}
    ids = [j["id"] for j in sims]
    with session() as s:
        texts = dict(s.execute(select(Job.id, Job.description_text).where(Job.id.in_(ids))).all())
    docs = [texts.get(i) or "" for i in ids]
    of = len(docs)
    terms: dict[str, SimilarTerm] = {}
    # 1. catalogue skills, counted by posting
    for doc in docs:
        for name in set(extract_skills(doc)):
            t = terms.get(name.lower()) or SimilarTerm(name, "skill", 0, of)
            t.count += 1
            terms[name.lower()] = t
    # 2. distinctive phrases: mean TF-IDF weight over the set, kept when enough postings use them
    pool_ids, vec, matrix, pool_mean = _pool(_pool_stamp())
    index = {jid: k for k, jid in enumerate(pool_ids)}
    rows = [index[i] for i in ids if i in index]
    with session() as s:
        company_words = {w.lower() for (name,) in s.execute(select(Company.name)) for w in name.split()}
    if rows:
        sub = matrix[rows]
        mean = np.asarray(sub.mean(axis=0)).ravel()
        present = np.asarray((sub > 0).sum(axis=0)).ravel()
        vocab = vec.get_feature_names_out()
        distinct = mean / np.maximum(pool_mean, 1e-9)
        order = np.argsort(-(mean * np.minimum(distinct, 10)))
        for k in order[:600]:
            phrase = vocab[k]
            words = phrase.split()
            if (present[k] < max(2, MIN_DOC_FRACTION * len(rows)) or distinct[k] < MIN_DISTINCTIVENESS
                    or len(words) < 2 or any(w in _GENERIC or w in company_words for w in words) or len(phrase) < 6):
                continue  # single words come from the catalogue; phrases must be distinctive for this kind of role
            key = phrase.lower()
            if key in terms or any(key in existing or existing in key for existing in terms):
                continue
            terms[key] = SimilarTerm(phrase, "phrase", int(present[k]), of)
            if len(terms) >= n_terms * 2:
                break
    ranked = sorted(terms.values(), key=lambda t: (-t.count, t.kind != "skill", t.term))[:n_terms]
    return {"similar": sims, "terms": [t.as_dict() for t in ranked]}


def context_block(ctx: dict) -> str:
    """The SIMILAR ROLES section of the user message."""
    if not ctx.get("terms"):
        return "SIMILAR ROLES: none found."
    of = ctx["terms"][0]["of"]
    lines = [f"- {t['term']} ({t['count']} of {of})" for t in ctx["terms"]]
    return f"SIMILAR ROLES ({of} postings with a similar title):\n" + "\n".join(lines)
