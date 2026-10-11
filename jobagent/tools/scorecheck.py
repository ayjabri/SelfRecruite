"""Score check: how the fast score compares with what the user marked. No LLM.

A mark the model was trained on says little about the model: it has seen the answer. So the model is
judged on *held-out* scores (each marked job scored by models that were trained without it, stored at
training time), and on the *forward test*: the score a job had on the page at the moment it was marked.
"""
from collections import Counter

from pydantic import BaseModel, Field
from sklearn.metrics import roc_auc_score
from sqlalchemy import select

from jobagent.db import session
from jobagent.groups import Segments
from jobagent.models import Company, Job, PreferenceModelRow, UserJob
from jobagent.tools.preference import preference_status
from jobagent.tools.registry import tool_spec
from jobagent.users import get_profile, resolve_user

BANDS = [(0, 30), (31, 50), (51, 70), (71, 85), (86, 100)]
MIN_FOR_AUC = 5   # of each kind, before an AUC is quoted for a slice


class ScoreCheckInput(BaseModel):
    user_id: int | None = Field(None, description="Whose marks; default: the current user")
    threshold: int = Field(60, ge=1, le=99, description="A score at or above this counts as 'the score says interested'")
    limit: int = Field(15, ge=1, le=100, description="How many disagreements to list each way")


class ScoreCheckResult(BaseModel):
    threshold: int = 60
    marks: int = 0
    interested: int = 0
    status: dict = {}              # last training, whether it is out of date (preference_status)
    held_out: dict = {}            # n, auc, rule_auc, precision, recall, pending
    bands: list[dict] = []         # held-out score band -> jobs, interested
    interested_scored_low: list[dict] = []
    not_interested_scored_high: list[dict] = []
    by_group: list[dict] = []
    by_company: list[dict] = []
    capped: dict = {}              # interested marks the page shows at 30 or below because of a gate or rule
    forward: dict = {}             # the score at the moment of marking vs the mark
    history: list[dict] = []
    error: str | None = None


def _auc(y: list[int], scores: list[float]) -> float | None:
    if min(sum(y), len(y) - sum(y)) < MIN_FOR_AUC:
        return None
    return round(float(roc_auc_score(y, scores)), 3)


def _slice(name: str, rows: list[dict], threshold: int) -> dict:
    y = [r["y"] for r in rows]
    agree = sum((r["held"] >= threshold) == bool(r["y"]) for r in rows)
    return {"name": name, "marks": len(rows), "interested": sum(y), "auc": _auc(y, [r["held"] for r in rows]),
            "agree": agree, "agree_pct": round(100 * agree / len(rows)) if rows else None}


@tool_spec(
    name="score_check",
    description=(
        "Compare the user's fast score with their interested / not-interested marks: held-out accuracy of the "
        "learned model against the rule score, the share interested per score band, the jobs where score and "
        "mark disagree most, accuracy per company group and company, interested marks held down by a gate or "
        "rule, how the score did on jobs at the moment they were marked, and accuracy at each past training."
    ),
)
def score_check(args: ScoreCheckInput) -> ScoreCheckResult:
    uid = resolve_user(args.user_id)
    t = args.threshold
    out = ScoreCheckResult(threshold=t, status=preference_status(uid))
    segs = Segments(get_profile(uid)["targets"])
    with session() as s:
        model = s.execute(select(PreferenceModelRow.trained_at, PreferenceModelRow.diagnostics)
                          .where(PreferenceModelRow.user_id == uid)).first()
        marked = s.execute(
            select(Job.id, Job.title, Company.name, UserJob.user_label, UserJob.fit_score, UserJob.fit_details,
                   UserJob.label_score, UserJob.user_labeled_at, UserJob.in_scope)
            .join(UserJob, (UserJob.job_id == Job.id) & (UserJob.user_id == uid))
            .join(Company, Company.id == Job.company_id)
            .where(UserJob.user_label.is_not(None), Job.duplicate_of.is_(None))
        ).all()
    diagnostics = (model.diagnostics if model else None) or {}
    unseen = diagnostics.get("held_out") or {}
    out.history = diagnostics.get("history") or []
    out.marks, out.interested = len(marked), sum(m.user_label == "interested" for m in marked)

    rows = []
    for m in marked:
        details = m.fit_details or {}
        rows.append({
            "job_id": m.id, "title": m.title, "company": m.name, "y": int(m.user_label == "interested"),
            "held": unseen.get(str(m.id)), "shown": m.fit_score, "rule": details.get("rule_score"),
            "gates": details.get("gates") or [], "group": segs.group(m.name) or "No group", "in_scope": bool(m.in_scope),
            "label_score": m.label_score, "labeled_at": m.user_labeled_at,
        })
    judged = [r for r in rows if r["held"] is not None]
    brief = lambda r: {k: r[k] for k in ("job_id", "title", "company", "held", "shown", "gates")}  # noqa: E731

    if judged:
        y = [r["y"] for r in judged]
        said_yes = [r for r in judged if r["held"] >= t]
        with_rule = [r for r in judged if r["rule"] is not None]
        out.held_out = {
            "marks": len(judged), "interested": sum(y),
            "auc": _auc(y, [r["held"] for r in judged]),
            "rule_auc": _auc([r["y"] for r in with_rule], [r["rule"] for r in with_rule]),
            "scored_high": len(said_yes), "scored_high_interested": sum(r["y"] for r in said_yes),
            "precision_pct": round(100 * sum(r["y"] for r in said_yes) / len(said_yes)) if said_yes else None,
            "recall_pct": round(100 * sum(r["y"] for r in said_yes) / sum(y)) if sum(y) else None,
        }
        out.bands = [{"low": lo, "high": hi, "marks": len(b), "interested": sum(r["y"] for r in b)}
                     for lo, hi in BANDS for b in [[r for r in judged if lo <= r["held"] <= hi]]]
        out.interested_scored_low = [brief(r) for r in sorted((r for r in judged if r["y"] and r["held"] < t), key=lambda r: r["held"])[:args.limit]]
        out.not_interested_scored_high = [brief(r) for r in sorted((r for r in judged if not r["y"] and r["held"] >= t), key=lambda r: -r["held"])[:args.limit]]
        for key, target in (("group", out.by_group), ("company", out.by_company)):
            names = sorted({r[key] for r in judged}, key=lambda n: (n == "No group", n.lower()))
            target.extend(_slice(n, [r for r in judged if r[key] == n], t) for n in names)
    out.held_out["pending"] = len(rows) - len(judged)   # marked since the last training, or never scored

    # interested, yet the page shows 30 or less: a gate or a company/group rule holds it down, whatever the model thinks
    held_down = [r for r in rows if r["y"] and (r["gates"] or not r["in_scope"])]
    reasons = Counter()
    for r in held_down:
        for g in (r["gates"] or ["hidden or out of your scope"]):
            reasons["not remote, and outside your allowed states" if g.startswith("not remote") else g] += 1
    out.capped = {"interested": len(held_down), "reasons": [{"reason": k, "marks": v} for k, v in reasons.most_common()]}

    # the forward test: marks made with a score on the page that could not have learned from them
    fwd = [r for r in rows if r["label_score"] is not None]
    since = [r for r in fwd if model and r["labeled_at"] and r["labeled_at"] > model.trained_at]
    count = lambda rs: {"marks": len(rs), "agree": sum((r["label_score"] >= t) == bool(r["y"]) for r in rs)}  # noqa: E731
    misses = sorted((r for r in fwd if (r["label_score"] >= t) != bool(r["y"])), key=lambda r: r["labeled_at"] or 0, reverse=True)
    out.forward = {"all": count(fwd), "since_training": count(since),
                   "auc": _auc([r["y"] for r in fwd], [r["label_score"] for r in fwd]),
                   "misses": [{"job_id": r["job_id"], "title": r["title"], "company": r["company"], "score_when_marked": r["label_score"],
                               "mark": "interested" if r["y"] else "not interested"} for r in misses[:args.limit]]}
    return out
