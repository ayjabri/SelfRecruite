import pytest

from jobagent.groups import Segments, targets_sha, title_family, validate

FAMILIES = {
    "G_tpm": {"weight": 1.0, "keywords": ["technical program manager", "tpm"]},
    "A_product": {"weight": 0.85, "keywords": ["program manager", "product manager"]},
    "E_treasury": {"weight": 1.0, "keywords": ["treasury"]},
    "D_ai_ml": {"weight": 0.9, "keywords": ["machine learning", "ai"]},
}
TARGETS = {
    "title_families": FAMILIES,
    "company_groups": {
        "Tech": {"companies": ["NVIDIA", "Anthropic"], "interest": {"families": ["G_tpm"], "keywords": [], "otherwise": "cap"}},
        "Banks": {"companies": ["Citi", "JPMorgan Chase", "Mastercard", "US Bank"], "interest": {}},
    },
    "company_interest": {"Citi": {"families": ["E_treasury"], "keywords": ["alm", "liquidity"], "otherwise": "hide"}},
}


def test_group_rule_caps_roles_that_are_not_listed():
    segs = Segments(TARGETS)
    ok = segs.check("NVIDIA", "Senior Technical Program Manager, AI Clusters")   # also matches D_ai_ml: any listed family is enough
    assert ok.qualifies and ok.action is None and ok.segment == "Tech"
    capped = segs.check("nvidia", "Machine Learning Engineer")
    assert (capped.action, capped.reason) == ("cap", "not a role you target at Tech companies")


def test_company_statement_replaces_its_group_and_keywords_search_the_bullets():
    segs = Segments(TARGETS)
    assert segs.check("Citi", "Treasury Director").qualifies
    assert segs.check("Citi", "Risk Analytics VP", ["Build ALM models for the balance sheet"]).qualifies   # family OR keyword
    hidden = segs.check("Citi", "Risk Analytics VP", ["Stay calm under pressure"])                          # whole words only
    assert (hidden.action, hidden.segment, hidden.group, hidden.rule_from) == ("hide", "Citi", "Banks", "company")
    assert segs.segment("Citi") == "Citi" and segs.segment("JPMorgan Chase") == "Banks"


def test_no_statement_and_no_group_restrict_nothing():
    segs = Segments(TARGETS)
    assert segs.check("JPMorgan Chase", "Anything At All").action is None
    v = segs.check("Stripe", "Anything")
    assert (v.action, v.group, v.segment) == (None, None, "none")
    assert not Segments({}).has_rules and Segments(TARGETS).needs_bullets


def test_company_names_match_without_case_spaces_or_dots():
    segs = Segments(TARGETS)
    assert segs.group("Master Card") == "Banks" and segs.group("U.S. Bank") == "Banks" and segs.group("citi") == "Banks"


def test_a_family_removed_from_the_profile_no_longer_restricts():
    targets = {**TARGETS, "title_families": {k: v for k, v in FAMILIES.items() if k != "G_tpm"}}
    assert Segments(targets).check("NVIDIA", "Machine Learning Engineer").action is None


def test_tpm_family_outranks_product_for_tpm_titles():
    assert title_family("Senior Technical Program Manager", FAMILIES) == (1.0, "G_tpm")
    assert title_family("Program Manager, Brand", FAMILIES) == (0.85, "A_product")
    assert title_family("Accountant", FAMILIES) == (0.0, None)


def test_validate_tidies_and_rejects():
    out = validate({"company_groups": {" Tech ": {"companies": ["NVIDIA", "nvidia", " "], "interest": {"families": ["G_tpm"]}},
                                       "Banks": {"companies": ["Citi"], "interest": {"otherwise": "hide"}}},
                    "company_interest": {"Citi": {"keywords": ["alm "]}, "BNY": {}}}, FAMILIES)
    assert out["company_groups"]["Tech"] == {"companies": ["NVIDIA"], "interest": {"families": ["G_tpm"], "keywords": [], "otherwise": "cap"}}
    assert out["company_groups"]["Banks"]["interest"] == {}            # nothing listed = no restriction, whatever "otherwise" says
    assert out["company_interest"] == {"Citi": {"families": [], "keywords": ["alm"], "otherwise": "cap"}}
    with pytest.raises(ValueError, match="two groups"):
        validate({"company_groups": {"A": {"companies": ["Citi"]}, "B": {"companies": ["CITI"]}}}, FAMILIES)
    with pytest.raises(ValueError, match="unknown role families"):
        validate({"company_groups": {"A": {"interest": {"families": ["nope"]}}}}, FAMILIES)
    with pytest.raises(ValueError, match="cap or hide"):
        validate({"company_interest": {"Citi": {"keywords": ["x"], "otherwise": "boost"}}}, FAMILIES)


def test_targets_sha_changes_with_any_setting():
    assert targets_sha(TARGETS) == targets_sha(dict(reversed(list(TARGETS.items()))))
    assert targets_sha(TARGETS) != targets_sha({**TARGETS, "company_interest": {}})


def test_groups_save_keeps_the_rest_of_the_targets_and_adds_template_families(monkeypatch):
    from jobagent.web import configs
    monkeypatch.setattr(configs, "profile_defaults", lambda: {"targets": {"title_families": {"G_tpm": FAMILIES["G_tpm"]}}})
    current = {"weights": {"domain": 0.1}, "title_families": {"E_treasury": FAMILIES["E_treasury"]}, "company_groups": {"Old": {}}}
    new = configs._groups_save(current, {"company_groups": {"Tech": {"companies": ["NVIDIA"], "interest": {"families": ["G_tpm"]}}},
                                         "add_families": ["G_tpm", "not_in_template"]})
    assert new["weights"] == {"domain": 0.1} and set(new["title_families"]) == {"E_treasury", "G_tpm"}
    assert list(new["company_groups"]) == ["Tech"] and new["company_interest"] == {}
