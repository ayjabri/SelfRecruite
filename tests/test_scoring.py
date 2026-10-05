from jobagent.extract.core import derive_seniority
from jobagent.parse.schema import ParsedJob
from jobagent.tools.scoring import combine, domain_fit, gates, rescale, title_family

FAMILIES = {"treasury": {"weight": 1.0, "keywords": ["treasury", "alm"]},
            "analytics": {"weight": 0.8, "keywords": ["analytics"]}}


def test_rescale_clips_to_the_band():
    assert rescale(0.50, 0.55, 0.80) == 0 and rescale(0.90, 0.55, 0.80) == 1
    assert abs(rescale(0.675, 0.55, 0.80) - 0.5) < 1e-6


def test_title_family_takes_the_highest_matching_weight():
    assert title_family("Treasury Analytics Lead", FAMILIES) == (1.0, "treasury")
    assert title_family("Marketing Analytics", FAMILIES) == (0.8, "analytics")
    assert title_family("Calm Engineer", FAMILIES) == (0.0, None)   # whole words only


def test_domain_fit():
    cfg = {"companies": ["Citi"], "keywords": ["bank", "treasury", "payments"]}
    assert domain_fit("citi", "Engineer", [], cfg) == 1.0
    assert abs(domain_fit("Acme", "Treasury lead", ["bank experience"], cfg) - 2 / 3) < 1e-6
    assert domain_fit("Acme", "Engineer", [], cfg) == 0.0


def _job(**kw):
    return ParsedJob(sections_quality="unlabelled", **kw)


def test_gates():
    cfg = {"clearance_required": True, "phd_required": True, "pay_floor": 180000, "allowed_states": ["NY"]}
    bullets = lambda *b: [{"heading": "", "label": "other", "bullets": list(b)}]
    assert gates(_job(clearance_required=True), None, cfg) == ["security clearance required"]
    assert gates(_job(sections=bullets("PhD in Statistics")), None, cfg) == ["PhD required"]
    assert gates(_job(sections=bullets("BS, MS, or PhD in Computer Science")), None, cfg) == []
    assert gates(_job(sections=bullets("Master's degree (PhD preferred)")), None, cfg) == []
    assert gates(_job(comp_period="year"), 150000, cfg) == ["top of pay range below 180,000"]
    assert gates(_job(comp_period="year"), 250000, cfg) == []
    ca = [{"city": "San Francisco", "state": "CA", "country": "US"}]
    assert gates(_job(locations=ca), None, cfg) == ["not remote and located in CA"]
    assert gates(_job(locations=ca, workplace_type="remote"), None, cfg) == []
    assert gates(_job(locations=ca), None, {}) == []   # every gate is off unless configured


def test_combine_and_gate_cap():
    assert combine(1, 1, 1, 1, 1, None, []) == 100
    assert combine(0.5, 0.5, 1, 1, 0, None, []) == round(100 * (0.45 * .5 + 0.20 * .5 + 0.15 + 0.10))
    assert combine(1, 1, 1, 1, 1, None, ["clearance"]) == 30
    assert combine(0.1, 0.1, 0, 0, 0, None, ["clearance"]) == 6   # the cap never raises a score


def test_bank_titles():
    assert derive_seniority("Vice President, Treasury", None, "unknown") == "vp_plus"
    assert derive_seniority("Vice President, Treasury", None, "unknown", bank=True) == "senior"
    assert derive_seniority("Liquidity Analytics, Executive Director", None, "unknown", bank=True) == "director"
    assert derive_seniority("Managing Director, Markets", None, "unknown", bank=True) == "vp_plus"
    assert derive_seniority("Quant Analytics, Senior Associate", None, "unknown", bank=True) == "mid"
