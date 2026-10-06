import pytest

from jobagent.tools.common import NormalizedPosting
from jobagent.tools.filtering import evaluate
from jobagent.tools.geo import classify_location, phrase_regex

CFG = {
    "include": phrase_regex(["treasury", "ai", "product manager"]),
    "exclude": phrase_regex(["intern", "sales"]),
    "countries": ("US",), "extra_us": (), "extra_non_us": (),
}


def _p(title, location="New York, NY"):
    return NormalizedPosting(company="c", title=title, location_text=location)


@pytest.mark.parametrize("text,expected", [
    ("San Francisco, CA | New York City, NY", "us"),
    ("US, CA, Santa Clara", "us"),
    ("Remote-Friendly, United States", "us"),
    ("Plano, TX", "us"),                       # state code, city not in any list
    ("Atlanta, Georgia", "us"),
    ("SF, NYC, SEA, CHI", "us"),
    ("London, UK; Remote-Friendly, United States", "us"),   # any US segment keeps it
    ("Tel Aviv, IL", "non_us"),                # IL is Israel here, not Illinois
    ("Toronto, CA", "non_us"),
    ("Munich, DE", "non_us"),
    ("GLASGOW, LANARKSHIRE, United Kingdom", "non_us"),
    ("Dublin or London", "non_us"),
    ("Remote - India", "non_us"),
    ("Remote", "unknown"),
    ("LOCATION", "unknown"),
    ("", "unknown"),
])
def test_classify_location(text, expected):
    assert classify_location(text)[0] == expected


def test_phrases_match_whole_words_only():
    r = phrase_regex(["ai", "intern"])
    assert r.search("Applied AI Engineer") and r.search("Intern, Finance")
    assert not r.search("Maintenance Lead") and not r.search("Internal Audit Manager")


def test_evaluate_order_and_reasons():
    assert evaluate(_p("Treasury Analyst"), CFG) == (True, "matched")
    assert evaluate(_p("Treasury Sales Manager"), CFG) == (False, "excluded title keyword: sales")
    assert evaluate(_p("Relationship Banker"), CFG) == (False, "no target title keyword")
    assert evaluate(_p("AI Product Manager", "Singapore"), CFG) == (False, "outside US: Singapore")
    assert evaluate(_p("AI Product Manager", "Remote"), CFG)[0]  # unclear location is kept


def test_crawl_keeps_every_title(monkeypatch):
    """The crawl filters by location only; title keywords belong to each user's scope."""
    from jobagent.tools import filtering
    monkeypatch.setattr(filtering, "_profile_states", lambda: ("NY", "NJ"))
    filtering.load_filters.cache_clear()
    cfg = filtering.load_filters()
    assert cfg["include"] is None and cfg["exclude"] is None and cfg["allowed_states"] == ("NY", "NJ")
    assert evaluate(_p("Relationship Banker", "New York, NY"), cfg) == (True, "matched")
    assert evaluate(_p("Relationship Banker", "Plano, TX"), cfg)[0] is False
    filtering.load_filters.cache_clear()


def test_user_scope_applies_title_and_states():
    from jobagent.tools.scoring import job_in_scope
    scope = {"title": {"include": ["treasury"], "exclude": ["sales"]}, "location": {"allowed_states": ["NY"], "remote_ok": True}}
    ny, tx = [{"city": "New York", "state": "NY"}], [{"city": "Plano", "state": "TX"}]
    assert job_in_scope("Treasury Lead", ny, "hybrid", scope) == (True, "matched")
    assert job_in_scope("Treasury Sales Lead", ny, "hybrid", scope)[1].startswith("excluded")
    assert job_in_scope("Banker", ny, "hybrid", scope)[1] == "no target title keyword"
    assert job_in_scope("Treasury Lead", tx, "hybrid", scope)[1].startswith("outside NY")
    assert job_in_scope("Treasury Lead", tx, "remote", scope)[0] is True
    assert job_in_scope("Treasury Lead", [], "hybrid", scope)[0] is True


@pytest.mark.parametrize("location,kept", [
    ("New York, NY", True),
    ("Jersey City, NJ", True),
    ("Plano, TX", False),
    ("Charlotte, NC | New York, NY", True),      # any allowed segment keeps it
    ("Remote - US", True),                        # remote is kept wherever
    ("United States", True),                      # no state readable: kept
])
def test_state_gate(location, kept):
    cfg = {**CFG, "allowed_states": ("NY", "NJ")}
    assert evaluate(_p("treasury lead", location), cfg)[0] is kept
    assert evaluate(_p("treasury lead", location), {**CFG, "allowed_states": ()})[0] is True
