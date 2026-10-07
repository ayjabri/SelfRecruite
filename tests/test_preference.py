import numpy as np

from jobagent.tools.preference import Example, PreferenceModel, cross_validated_auc, example_from


def _examples(n=80, seed=0):
    rng = np.random.default_rng(seed)
    out, labels = [], []
    for i in range(n):
        liked = i % 2 == 0
        emb = rng.normal(size=16) + (1.5 if liked else -1.5)   # a learnable signal in the embedding
        out.append(Example(title="Treasury Manager" if liked else "Sales Associate", company="Acme",
                           seniority="lead", title_family="none", components=[0.5] * 5, embedding=emb.tolist()))
        labels.append(int(liked))
    return out, labels


def test_model_learns_and_scores_unseen_categories():
    examples, labels = _examples()
    model = PreferenceModel().fit(examples, labels)
    new = Example(title="Treasury Manager", company="Never Seen Inc", seniority="director", title_family="x",
                  components=[0.5] * 5, embedding=(np.ones(16) * 1.5).tolist())
    assert model.predict([new])[0] > 0.8
    auc, spread = cross_validated_auc(examples, labels, repeats=1)
    assert auc > 0.95 and spread == 0.0


def test_example_from_tolerates_missing_details():
    e = example_from("T", "C", None, None, [1.0, 0.0])
    assert (e.seniority, e.title_family, e.components) == ("unknown", "none", [0.0] * 5)


def test_marks_fingerprint_changes_when_a_mark_changes():
    from types import SimpleNamespace as R

    from jobagent.tools.preference import marks_fingerprint
    a = [R(id=1, user_label="interested"), R(id=2, user_label="not_interested")]
    assert marks_fingerprint(a) == marks_fingerprint(list(reversed(a)))           # order does not matter
    assert marks_fingerprint(a) != marks_fingerprint(a[:1])                         # a mark removed
    assert marks_fingerprint(a) != marks_fingerprint([a[0], R(id=2, user_label="interested")])  # flipped


def _status(**kw):
    return {"interested": 20, "not_interested": 20, "can_train": True, "trained_at": "2026-10-06T10:00:00",
            "out_of_date": False, **kw}


def test_refresh_trains_only_when_needed(monkeypatch):
    from jobagent.tools import preference as pref
    trained = []
    monkeypatch.setattr(pref, "train_preference_model", lambda args: trained.append(args) or pref.TrainResult(trained=True, rescored=7))
    cases = [
        (_status(can_train=False, interested=3), "need at least"),
        (_status(), "no marks changed"),
        (_status(out_of_date=True), None),
        (_status(trained_at=None), None),          # never trained, enough marks
    ]
    for status, skipped in cases:
        monkeypatch.setattr(pref, "preference_status", lambda uid, s=status: s)
        trained.clear()
        out = pref.refresh_preference_model(pref.TrainInput(user_id=1))
        assert out.error is None
        if skipped:
            assert skipped in out.skipped and not trained and not out.trained
        else:
            assert out.trained and out.rescored == 7 and out.skipped is None and len(trained) == 1
