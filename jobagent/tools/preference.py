"""Preference model: learns from the jobs a user marked which jobs they are likely to want."""
import hashlib
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
from jobagent.groups import NO_SEGMENT, Segments, title_family
from jobagent.models import Company, Job, PreferenceModelRow, UserJob
from jobagent.tools.registry import tool_spec
from jobagent.users import get_profile, resolve_user

log = logging.getLogger(__name__)

COMPONENTS = ["required", "preferred", "title_family", "seniority", "domain"]
MIN_PER_CLASS = 15
HISTORY = 60   # trainings kept for the score check
FEATURES = 2   # 2: + the company segment and segment x role family (jobagent.groups)


class Example(BaseModel):
    """What the model sees about one job."""
    title: str
    company: str
    seniority: str
    title_family: str
    components: list[float]
    embedding: list[float]
    segment: str = NO_SEGMENT   # the company if the user singled it out, else its group


class PreferenceModel:
    """Logistic regression over title words, company, seniority, title family, the rule-score
    components, the job embedding, and the user's company segment crossed with the role family:
    the same role can be wanted in one group of companies and not in another."""

    def __init__(self, C: float = 3.0):
        self.tfidf = TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)
        self.onehot = OneHotEncoder(handle_unknown="ignore")
        self.segments = OneHotEncoder(handle_unknown="ignore")
        self.clf = LogisticRegression(C=C, max_iter=3000)
        self.features = FEATURES
        self.meta: dict = {}

    def _matrix(self, examples: list[Example], fit: bool):
        titles = [e.title for e in examples]
        cats = [[e.company, e.seniority, e.title_family] for e in examples]
        emb = np.array([e.embedding for e in examples], dtype=np.float64)
        emb /= np.linalg.norm(emb, axis=1, keepdims=True)
        dense = np.hstack([emb, np.array([e.components for e in examples], dtype=np.float64)])
        t = self.tfidf.fit_transform(titles) if fit else self.tfidf.transform(titles)
        c = self.onehot.fit_transform(cats) if fit else self.onehot.transform(cats)
        parts = [t, c, csr_matrix(dense)]
        if getattr(self, "features", 1) >= 2:   # a model saved before segments existed keeps working until retrained
            seg = [[e.segment, f"{e.segment}|{e.title_family}"] for e in examples]
            parts.append(self.segments.fit_transform(seg) if fit else self.segments.transform(seg))
        return hstack(parts).tocsr()

    def fit(self, examples: list[Example], labels: list[int]) -> "PreferenceModel":
        self.clf.fit(self._matrix(examples, fit=True), labels)
        return self

    def predict(self, examples: list[Example]) -> list[float]:
        return self.clf.predict_proba(self._matrix(examples, fit=False))[:, 1].tolist()


def example_from(title, company, seniority, fit_details, embedding, segment=None, family=...) -> Example:
    """`segment` and `family` default to what the last scoring stored; training passes them fresh from
    the profile so a change of groups or role families is learned without waiting for a re-score."""
    details = fit_details or {}
    comps = details.get("components") or {}
    return Example(
        title=title, company=company, seniority=seniority or "unknown",
        title_family=(details.get("title_family") if family is ... else family) or "none",
        components=[float(comps.get(k, 0.0)) for k in COMPONENTS],
        embedding=[float(x) for x in embedding],
        segment=segment or details.get("segment") or NO_SEGMENT,
    )


def held_out(examples: list[Example], labels: list[int], repeats: int = 5) -> tuple[float, float, list[float]]:
    """Repeated 5-fold splits, refitting the whole model each fold: mean and spread of AUC, and for every
    example its average prediction from the models that did not train on it."""
    y = np.array(labels)
    aucs, total = [], np.zeros(len(y))
    for seed in range(repeats):
        scores = np.zeros(len(y))
        for train, test in StratifiedKFold(5, shuffle=True, random_state=seed).split(examples, y):
            m = PreferenceModel().fit([examples[i] for i in train], y[train].tolist())
            scores[test] = m.predict([examples[i] for i in test])
        aucs.append(roc_auc_score(y, scores))
        total += scores
    return float(np.mean(aucs)), float(np.std(aucs)), (total / repeats).tolist()


def cross_validated_auc(examples: list[Example], labels: list[int], repeats: int = 5) -> tuple[float, float]:
    return held_out(examples, labels, repeats)[:2]


def _marked_rows(uid: int) -> list:
    """The user's marked jobs the model can learn from: scored, embedded and not merged away."""
    with session() as s:
        return s.execute(
            select(Job.id, Job.title, Company.name, Job.seniority, UserJob.fit_details, Job.embedding, UserJob.user_label)
            .join(UserJob, (UserJob.job_id == Job.id) & (UserJob.user_id == uid))
            .join(Company, Company.id == Job.company_id)
            .where(UserJob.user_label.is_not(None), UserJob.fit_details.is_not(None), Job.embedding.is_not(None),
                   Job.duplicate_of.is_(None))
        ).all()


def _context(uid: int) -> tuple[Segments, dict]:
    """The user's company segments and role families, as training and the staleness check see them."""
    targets = get_profile(uid)["targets"]
    return Segments(targets), targets.get("title_families") or {}


def marks_fingerprint(rows, segs: Segments | None = None, families: dict | None = None) -> str:
    """Changes whenever a mark is added, removed or flipped, or a marked job moves to another company
    segment or role family: tells whether the model is out of date."""
    segs = segs or Segments(None)
    marks = sorted(f"{r.id}:{r.user_label}:{segs.segment(r.name)}:{title_family(r.title, families)[1]}" for r in rows)
    return hashlib.sha256(",".join(marks).encode()).hexdigest()[:16]


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
    rows = _marked_rows(uid)
    segs, families = _context(uid)
    labels = [int(r.user_label == "interested") for r in rows]
    out.interested, out.not_interested = sum(labels), len(labels) - sum(labels)
    if min(out.interested, out.not_interested) < MIN_PER_CLASS:
        out.error = f"need at least {MIN_PER_CLASS} jobs marked each way; have {out.interested} interested, {out.not_interested} not"
        return out
    examples = [example_from(r.title, r.name, r.seniority, r.fit_details, r.embedding,
                             segment=segs.segment(r.name), family=title_family(r.title, families)[1]) for r in rows]
    rule = [float((r.fit_details or {}).get("rule_score", 0)) for r in rows]
    out.rule_score_auc = round(float(roc_auc_score(labels, rule)), 3)
    auc, spread, unseen = held_out(examples, labels)
    out.model_auc, out.model_auc_spread = round(auc, 3), round(spread, 3)

    model = PreferenceModel().fit(examples, labels)
    now = datetime.now(timezone.utc)
    model.meta = {
        "trained_at": now.isoformat(), "labels": len(labels),
        "interested": out.interested, "cv_auc": out.model_auc, "rule_score_auc": out.rule_score_auc,
        "marks_fingerprint": marks_fingerprint(rows, segs, families), "features": FEATURES,
        "held_out": True,   # the held-out scores for the score check were stored with this training
    }
    buf = io.BytesIO()
    joblib.dump(model, buf)
    with session() as s, s.begin():
        row = s.get(PreferenceModelRow, uid) or PreferenceModelRow(user_id=uid, blob=b"")
        row.trained_at, row.meta, row.blob = now, model.meta, buf.getvalue()
        history = [*((row.diagnostics or {}).get("history") or []), {k: model.meta[k] for k in ("trained_at", "labels", "interested", "cv_auc", "rule_score_auc")}]
        row.diagnostics = {"held_out": {str(r.id): round(100 * p) for r, p in zip(rows, unseen)}, "history": history[-HISTORY:]}
        s.add(row)
    out.trained = True
    log.info("preference model for user %d trained on %d marks, held-out AUC %.3f (rule score %.3f)",
             uid, len(labels), auc, out.rule_score_auc)
    if args.rescore:
        from jobagent.tools.scoring import ScoreJobsInput, score_jobs_fast
        scored = score_jobs_fast(ScoreJobsInput(user_id=uid, rescore=True))
        out.rescored, out.error = scored.scored, scored.error
    return out


def preference_status(user_id: int | None = None) -> dict:
    """Where the user's model stands: marks usable for training, the last training, whether it is out of date."""
    uid = resolve_user(user_id)
    rows = _marked_rows(uid)
    interested = sum(r.user_label == "interested" for r in rows)
    with session() as s:
        row = s.execute(select(PreferenceModelRow.trained_at, PreferenceModelRow.meta)
                        .where(PreferenceModelRow.user_id == uid)).first()
    meta = (row.meta if row else None) or {}
    segs, families = _context(uid)
    return {
        "interested": interested, "not_interested": len(rows) - interested, "min_per_class": MIN_PER_CLASS,
        "can_train": min(interested, len(rows) - interested) >= MIN_PER_CLASS,
        "trained_at": row.trained_at.isoformat(timespec="seconds") if row else None,
        "labels_at_training": meta.get("labels"), "cv_auc": meta.get("cv_auc"), "rule_score_auc": meta.get("rule_score_auc"),
        "out_of_date": row is not None and (meta.get("marks_fingerprint") != marks_fingerprint(rows, segs, families)
                                             or meta.get("features") != FEATURES or not meta.get("held_out")),
    }


class RefreshResult(TrainResult):
    skipped: str | None = None   # why no training was needed or possible; not an error


@tool_spec(
    name="refresh_preference_model",
    description=(
        "Retrain the user's preference model if their marks changed since it was last trained (a mark added, "
        "removed or flipped) and they have enough marks each way; then re-score their jobs with it. Does "
        "nothing otherwise. Runs before the fast score in every scoring run, so marking jobs is enough."
    ),
)
def refresh_preference_model(args: TrainInput) -> RefreshResult:
    status = preference_status(args.user_id)
    out = RefreshResult(interested=status["interested"], not_interested=status["not_interested"])
    if not status["can_train"]:
        out.skipped = (f"need at least {MIN_PER_CLASS} jobs marked each way; have {status['interested']} "
                       f"interested, {status['not_interested']} not")
    elif status["trained_at"] and not status["out_of_date"]:
        out.skipped = "no marks changed since the last training"
    else:
        return RefreshResult(**train_preference_model(args).model_dump())
    return out
