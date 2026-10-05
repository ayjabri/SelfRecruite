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
