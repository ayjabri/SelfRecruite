import pytest

from jobagent.tools.common import NormalizedPosting
from jobagent.tools.filtering import evaluate
from jobagent.tools.geo import classify_location, phrase_regex

CFG = {
    "include": phrase_regex(["treasury", "ai", "product manager"]),
    "exclude": phrase_regex(["intern", "sales"]),
    "us_only": True, "extra_us": (), "extra_non_us": (),
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


def test_shipped_filters_file_loads():
    from jobagent.tools.filtering import load_filters
    cfg = load_filters()
    assert cfg["include"].search("Senior Treasury Analyst") and cfg["exclude"].search("Summer Internship")
