"""Preference model: learns from the jobs a user marked which jobs they are likely to want."""
import io
import logging
from datetime import datetime, timezone
from functools import lru_cache

import joblib
import numpy as np
from pydantic import BaseModel, Field
from scipy.sparse import csr_matrix, hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import OneHotEncoder
from sqlalchemy import select

from jobagent.db import session
from jobagent.models import Company, Job, PreferenceModelRow, UserJob
from jobagent.tools.registry import tool_spec
from jobagent.users import resolve_user

log = logging.getLogger(__name__)

COMPONENTS = ["required", "preferred", "title_family", "seniority", "domain"]
MIN_PER_CLASS = 15


class Example(BaseModel):
    """What the model sees about one job."""
    title: str
    company: str
    seniority: str
    title_family: str
    components: list[float]
    embedding: list[float]


class PreferenceModel:
    """Logistic regression over title words, company, seniority, title family,
    the rule-score components and the job embedding."""

    def __init__(self, C: float = 3.0):
        self.tfidf = TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)
        self.onehot = OneHotEncoder(handle_unknown="ignore")
        self.clf = LogisticRegression(C=C, max_iter=3000)
        self.meta: dict = {}

    def _matrix(self, examples: list[Example], fit: bool):
        titles = [e.title for e in examples]
        cats = [[e.company, e.seniority, e.title_family] for e in examples]
        emb = np.array([e.embedding for e in examples], dtype=np.float64)
        emb /= np.linalg.norm(emb, axis=1, keepdims=True)
        dense = np.hstack([emb, np.array([e.components for e in examples], dtype=np.float64)])
        t = self.tfidf.fit_transform(titles) if fit else self.tfidf.transform(titles)
        c = self.onehot.fit_transform(cats) if fit else self.onehot.transform(cats)
        return hstack([t, c, csr_matrix(dense)]).tocsr()

    def fit(self, examples: list[Example], labels: list[int]) -> "PreferenceModel":
        self.clf.fit(self._matrix(examples, fit=True), labels)
        return self

    def predict(self, examples: list[Example]) -> list[float]:
        return self.clf.predict_proba(self._matrix(examples, fit=False))[:, 1].tolist()


def example_from(title, company, seniority, fit_details, embedding) -> Example:
    comps = (fit_details or {}).get("components") or {}
    return Example(
        title=title, company=company, seniority=seniority or "unknown",
        title_family=(fit_details or {}).get("title_family") or "none",
        components=[float(comps.get(k, 0.0)) for k in COMPONENTS],
        embedding=[float(x) for x in embedding],
    )


def cross_validated_auc(examples: list[Example], labels: list[int], repeats: int = 5) -> tuple[float, float]:
    """Mean and spread of AUC over repeated 5-fold splits; every fold refits the whole model."""
    y = np.array(labels)
    aucs = []
    for seed in range(repeats):
        scores = np.zeros(len(y))
        for train, test in StratifiedKFold(5, shuffle=True, random_state=seed).split(examples, y):
            m = PreferenceModel().fit([examples[i] for i in train], y[train].tolist())
            scores[test] = m.predict([examples[i] for i in test])
        aucs.append(roc_auc_score(y, scores))
    return float(np.mean(aucs)), float(np.std(aucs))


@lru_cache(maxsize=64)
def _load(user_id: int, trained_at: str) -> PreferenceModel:
    with session() as s:
        row = s.get(PreferenceModelRow, user_id)
    model = joblib.load(io.BytesIO(row.blob))
    model.meta = row.meta or model.meta
    return model


def load_model(user_id: int | None = None) -> PreferenceModel | None:
    """The user's trained model, or None if there isn't one. Reloads when it is retrained."""
    uid = resolve_user(user_id)
    with session() as s:
        stamp = s.scalar(select(PreferenceModelRow.trained_at).where(PreferenceModelRow.user_id == uid))
    return _load(uid, stamp.isoformat()) if stamp else None


class TrainInput(BaseModel):
    user_id: int | None = Field(None, description="Whose marks to learn from; default: the current user")
    rescore: bool = Field(True, description="Re-score every in-scope job with the new model straight away")


class TrainResult(BaseModel):
    trained: bool = False
    interested: int = 0
    not_interested: int = 0
    rule_score_auc: float | None = None    # how well the rule-based score alone separates the marks
    model_auc: float | None = None         # the same for the trained model, on jobs held out from training
    model_auc_spread: float | None = None
    rescored: int | None = None
    error: str | None = None


@tool_spec(
    name="train_preference_model",
    description=(
        "Train the user's preference model on the jobs they marked interested or not interested, "
        "report how well it predicts held-out marks (AUC: 0.5 is chance, 1.0 is perfect), save it, and "
        "re-score the in-scope jobs with it. Run again whenever more jobs have been marked."
    ),
)
def train_preference_model(args: TrainInput) -> TrainResult:
    uid = resolve_user(args.user_id)
    out = TrainResult()
    with session() as s:
        rows = s.execute(
            select(Job.title, Company.name, Job.seniority, UserJob.fit_details, Job.embedding, UserJob.user_label)
            .join(UserJob, (UserJob.job_id == Job.id) & (UserJob.user_id == uid))
            .join(Company, Company.id == Job.company_id)
            .where(UserJob.user_label.is_not(None), UserJob.fit_details.is_not(None), Job.embedding.is_not(None),
                   Job.duplicate_of.is_(None))
        ).all()
    labels = [int(r.user_label == "interested") for r in rows]
    out.interested, out.not_interested = sum(labels), len(labels) - sum(labels)
    if min(out.interested, out.not_interested) < MIN_PER_CLASS:
        out.error = f"need at least {MIN_PER_CLASS} jobs marked each way; have {out.interested} interested, {out.not_interested} not"
        return out
    examples = [example_from(r.title, r.name, r.seniority, r.fit_details, r.embedding) for r in rows]
    rule = [float((r.fit_details or {}).get("rule_score", 0)) for r in rows]
    out.rule_score_auc = round(float(roc_auc_score(labels, rule)), 3)
    auc, spread = cross_validated_auc(examples, labels)
    out.model_auc, out.model_auc_spread = round(auc, 3), round(spread, 3)

    model = PreferenceModel().fit(examples, labels)
    now = datetime.now(timezone.utc)
    model.meta = {
        "trained_at": now.isoformat(), "labels": len(labels),
        "interested": out.interested, "cv_auc": out.model_auc, "rule_score_auc": out.rule_score_auc,
    }
    buf = io.BytesIO()
    joblib.dump(model, buf)
    with session() as s, s.begin():
        row = s.get(PreferenceModelRow, uid) or PreferenceModelRow(user_id=uid, blob=b"")
        row.trained_at, row.meta, row.blob = now, model.meta, buf.getvalue()
        s.add(row)
    out.trained = True
    log.info("preference model for user %d trained on %d marks, held-out AUC %.3f (rule score %.3f)",
             uid, len(labels), auc, out.rule_score_auc)
    if args.rescore:
        from jobagent.tools.scoring import ScoreJobsInput, score_jobs_fast
        scored = score_jobs_fast(ScoreJobsInput(user_id=uid, rescore=True))
        out.rescored, out.error = scored.scored, scored.error
    return out
