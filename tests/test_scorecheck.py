from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as R

import numpy as np

from jobagent.tools import scorecheck
from jobagent.tools.preference import Example, held_out
from jobagent.tools.scorecheck import ScoreCheckInput, _auc, _slice, score_check

TRAINED = datetime(2026, 10, 10, tzinfo=timezone.utc)


def test_held_out_returns_one_prediction_per_example():
    rng = np.random.default_rng(0)
    examples, labels = [], []
    for i in range(60):
        liked = i % 2 == 0
        examples.append(Example(title="Treasury Manager" if liked else "Sales Associate", company="Acme", seniority="lead",
                                title_family="none", components=[0.5] * 5, embedding=(rng.normal(size=8) + (1 if liked else -1)).tolist()))
        labels.append(int(liked))
    auc, spread, unseen = held_out(examples, labels, repeats=2)
    assert len(unseen) == 60 and all(0 <= p <= 1 for p in unseen) and auc > 0.9
    assert np.mean([p for p, y in zip(unseen, labels) if y]) > np.mean([p for p, y in zip(unseen, labels) if not y])


def test_auc_needs_enough_marks_each_way():
    assert _auc([1, 0, 1, 0], [90, 10, 80, 20]) is None
    assert _auc([1] * 5 + [0] * 5, [90] * 5 + [10] * 5) == 1.0


def test_slice_counts_agreement_at_the_threshold():
    rows = [{"y": 1, "held": 80}, {"y": 1, "held": 40}, {"y": 0, "held": 10}, {"y": 0, "held": 70}]
    assert _slice("Tech", rows, 60) == {"name": "Tech", "marks": 4, "interested": 2, "auc": None, "agree": 2, "agree_pct": 50}


class _Session:
    """Stands in for the database: the first query is the model row, the second the marked jobs."""
    def __init__(self, model, marked):
        self.results = [SimpleFirst(model), SimpleAll(marked)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, _):
        return self.results.pop(0)


class SimpleFirst:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class SimpleAll:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


def _mark(i, label, held, shown, company="Citi", gates=(), label_score=None, after=False, in_scope=True):
    return R(id=i, title=f"Job {i}", name=company, user_label=label, fit_score=shown, in_scope=in_scope,
             fit_details={"rule_score": 50, "gates": list(gates)}, label_score=label_score,
             user_labeled_at=TRAINED + timedelta(days=1 if after else -1)), held


def _run(monkeypatch, marks, history=()):
    rows = [m for m, _ in marks]
    model = R(trained_at=TRAINED, diagnostics={"held_out": {str(m.id): h for m, h in marks if h is not None}, "history": list(history)})
    monkeypatch.setattr(scorecheck, "session", lambda: _Session(model, rows))
    monkeypatch.setattr(scorecheck, "preference_status", lambda uid: {"trained_at": "2026-10-10T00:00:00"})
    monkeypatch.setattr(scorecheck, "get_profile", lambda uid: {"targets": {"company_groups": {"Banks": {"companies": ["Citi"]}}}})
    return score_check(ScoreCheckInput(user_id=1, threshold=60, limit=5))


def test_score_check_report(monkeypatch):
    marks = [_mark(i, "interested", 90, 95) for i in range(1, 7)] + [_mark(i, "not_interested", 10, 5) for i in range(7, 13)]
    marks += [
        _mark(20, "interested", 20, 30, company="NVIDIA", gates=["not a role you target at Tech companies"]),   # the model misses it, and a rule caps it
        _mark(21, "not_interested", 85, 80, company="NVIDIA"),                                                   # scored high, not wanted
        _mark(22, "interested", None, 70, label_score=70, after=True),                                            # marked since training: forward test only
        _mark(23, "not_interested", None, 75, label_score=75, after=True),
        _mark(24, "interested", 88, None, in_scope=False),                                                        # hidden by a rule
    ]
    out = _run(monkeypatch, marks, history=[{"trained_at": "2026-10-10T00:00:00", "labels": 14, "interested": 7, "cv_auc": 0.8, "rule_score_auc": 0.6}])
    assert (out.marks, out.interested) == (17, 9)
    h = out.held_out
    assert (h["marks"], h["pending"], h["scored_high"], h["scored_high_interested"]) == (15, 2, 8, 7)
    assert h["precision_pct"] == 88 and h["recall_pct"] == 88 and h["auc"] > 0.8 and h["rule_auc"] == 0.5
    assert [(b["low"], b["marks"], b["interested"]) for b in out.bands] == [(0, 7, 1), (31, 0, 0), (51, 0, 0), (71, 1, 0), (86, 7, 7)]
    assert [r["job_id"] for r in out.interested_scored_low] == [20] and [r["job_id"] for r in out.not_interested_scored_high] == [21]
    groups = {g["name"]: g for g in out.by_group}
    assert groups["Banks"]["agree"] == 13 and groups["No group"]["marks"] == 2 and groups["No group"]["agree"] == 0
    assert out.capped == {"interested": 2, "reasons": [{"reason": "not a role you target at Tech companies", "marks": 1},
                                                       {"reason": "hidden or out of your scope", "marks": 1}]}
    assert out.forward["all"] == {"marks": 2, "agree": 1} and out.forward["since_training"] == {"marks": 2, "agree": 1}
    assert [m["job_id"] for m in out.forward["misses"]] == [23] and len(out.history) == 1


def test_score_check_without_held_out_scores(monkeypatch):
    out = _run(monkeypatch, [_mark(1, "interested", None, 80), _mark(2, "not_interested", None, 20)])
    assert out.held_out == {"pending": 2} and out.bands == [] and out.error is None
