from jobagent.judge.core import _quote_in
from jobagent.tools.judging import _explanation


def test_quote_verification_tolerates_small_differences_only():
    text = ["Built and led a growing advanced-analytics team; the team began at approximately three people."]
    assert _quote_in("Built and led a growing advanced analytics team", text)
    assert _quote_in("the team began at approximately three people", text)
    assert not _quote_in("managed a trading desk", text)
    assert not _quote_in("", text) and not _quote_in(None, text)


def test_explanation_shape():
    items = [
        {"label": "required", "text": "10+ years in treasury", "cls": "Covered", "pct": 95, "missing": None},
        {"label": "required", "text": "Snowflake", "cls": "Gap", "pct": 0, "missing": "No Snowflake experience shown"},
        {"label": "preferred", "text": "MBA", "cls": "Gap", "pct": 10, "missing": "No MBA"},
    ]
    text = _explanation(78, items, "director", 1.0, [])
    assert text.startswith("78/100. Strong on 1 of 2 must-haves (10+ years in treasury).")
    assert "Gaps: No Snowflake experience shown; No MBA." in text
    assert text.endswith("Seniority: director, a good fit.")
    assert "Capped: security clearance required." in _explanation(30, items, "mid", 0.1, ["security clearance required"])
