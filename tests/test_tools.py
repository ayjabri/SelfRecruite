from jobagent.extract.core import apply_guardrails, derive_seniority
from jobagent.extract.schema import JobExtract
from jobagent.tools import TOOLS
from jobagent.tools.common import NormalizedPosting, clean_requisition_id, content_hash
from jobagent.tools import oracle, workday
from jobagent.tools.greenhouse import normalize


def _extract(**over) -> JobExtract:
    base = dict(
        company="x", job_title="x", requisition_id=None, locations=[], workplace_type="unknown",
        employment_type="full_time", seniority="unknown", summary="s", responsibilities=[],
        required_qualifications=[], preferred_qualifications=[], skills=[], min_years_experience=None,
        education=None, comp_min=None, comp_max=None, comp_period="unknown", comp_text=None,
        visa_sponsorship="unstated", clearance_required=False,
    )
    return JobExtract(**{**base, **over})


def test_every_tool_has_typed_io_and_description():
    assert TOOLS
    for spec in TOOLS.values():
        assert spec.description
        assert spec.input_model.model_json_schema()["type"] == "object"
        assert spec.output_model.model_json_schema()["type"] == "object"


def test_placeholder_requisition_ids_are_dropped():
    assert clean_requisition_id("See Opening ID") is None
    assert clean_requisition_id(" n/a ") is None
    assert clean_requisition_id("") is None
    assert clean_requisition_id("REQ-1234") == "REQ-1234"


def test_content_hash_ignores_case_and_whitespace():
    assert content_hash("Hello   World\n") == content_hash("hello world")
    assert content_hash("a") != content_hash("b")


def test_greenhouse_normalize_unescapes_and_reads_pay():
    n = normalize({
        "title": " Engineer ", "company_name": "Acme", "location": {"name": "NYC"},
        "content": "&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;", "requisition_id": "See Opening ID",
        "absolute_url": "u", "first_published": "2026-10-01T00:00:00-04:00",
        "pay_input_ranges": [{"min_cents": 15000000, "max_cents": 20000000}],
    })
    assert (n.title, n.company, n.location_text) == ("Engineer", "Acme", "NYC")
    assert n.description_text == "Build & ship"
    assert n.requisition_id is None
    assert (n.comp_min, n.comp_max, n.comp_period) == (150000, 200000, "year")


def test_seniority_title_beats_years_beats_model():
    assert derive_seniority("Senior Analyst", 1, "mid") == "senior"
    assert derive_seniority("Staff Engineer", None, "mid") == "lead"
    assert derive_seniority("Director, ALM", None, "mid") == "director"
    assert derive_seniority("Analyst", 5, "mid") == "senior"
    assert derive_seniority("Analyst", 3, "senior") == "mid"
    assert derive_seniority("Analyst", None, "mid") == "mid"


def test_guardrails_null_unsupported_comp_and_remote():
    p = NormalizedPosting(company="Acme", title="Analyst", location_text="Chicago",
                          description_text="Pay is $100,000 - $120,000 per year.")
    bad = apply_guardrails(_extract(workplace_type="remote", comp_min=90000, comp_max=120000,
                                    comp_period="year", comp_text="Pay is $100,000 - $120,000 per year."), p)
    assert bad.workplace_type == "unknown"
    assert (bad.comp_min, bad.comp_max, bad.comp_text) == (None, None, None)
    good = apply_guardrails(_extract(comp_min=100000, comp_max=120000, comp_period="year",
                                     comp_text="Pay is $100,000 - $120,000 per year."), p)
    assert (good.comp_min, good.comp_max) == (100000, 120000)
    assert good.company == "Acme" and good.job_title == "Analyst"


def test_ats_compensation_wins_over_model():
    p = NormalizedPosting(company="A", title="T", description_text="x", comp_min=1.0, comp_max=2.0, comp_period="hour")
    ex = apply_guardrails(_extract(comp_min=5, comp_max=6, comp_text="made up"), p)
    assert (ex.comp_min, ex.comp_max, ex.comp_period, ex.comp_text) == (1.0, 2.0, "hour", None)


def test_workday_normalize():
    n = workday.normalize({"company": "NVIDIA", "listing": {}, "detail": {
        "title": "Engineer", "jobDescription": "<p>Do <b>things</b></p>", "location": "US, CA, Santa Clara",
        "additionalLocations": ["US, TX, Austin"], "jobReqId": "JR1", "externalUrl": "u", "startDate": "2026-10-05",
    }})
    assert (n.company, n.requisition_id, n.url) == ("NVIDIA", "JR1", "u")
    assert n.location_text == "US, CA, Santa Clara; US, TX, Austin"
    assert "things" in n.description_text and "<" not in n.description_text
    assert workday._external_id({"bulletFields": ["JR9"], "externalPath": "/job/x/T_JR9"}) == "JR9"
    assert workday._external_id({"externalPath": "/job/x/T_JR9"}) == "JR9"


def test_oracle_normalize_joins_description_parts():
    n = oracle.normalize({"company": "JPMorgan Chase", "url": "u", "listing": {}, "detail": {
        "Id": "210", "Title": " Analyst ", "PrimaryLocation": "New York, NY, United States",
        "secondaryLocations": [{"Name": "Plano, TX, United States"}],
        "ExternalDescriptionStr": "<p>About</p>", "ExternalQualificationsStr": "<p>5 years</p>",
        "ExternalResponsibilitiesStr": None, "ExternalPostedStartDate": "2026-10-05T14:55:02+00:00",
    }})
    assert (n.title, n.requisition_id, n.url) == ("Analyst", "210", "u")
    assert n.location_text == "New York, NY, United States; Plano, TX, United States"
    assert "About" in n.description_text and "5 years" in n.description_text


def test_workday_posted_days_ago():
    from jobagent.tools.workday import posted_days_ago
    assert posted_days_ago("Posted Today") == 0
    assert posted_days_ago("Posted Yesterday") == 1
    assert posted_days_ago("Posted 3 Days Ago") == 3
    assert posted_days_ago("Posted 30+ Days Ago") == 31
    assert posted_days_ago(None) is None


def test_days_since():
    from datetime import date
    from jobagent.tools.common import days_since
    assert days_since("2026-10-03", today=date(2026, 10, 5)) == 2
    assert days_since("2026-10-05T14:55:02+00:00", today=date(2026, 10, 5)) == 0
    assert days_since("garbage") is None and days_since(None) is None


def test_hours_to_days_covers_the_window():
    from jobagent.tools.common import hours_to_days
    assert hours_to_days(None) is None
    assert hours_to_days(24) == 1    # "last 24 hours" needs today and yesterday on day-granular sources
    assert hours_to_days(48) == 2
    assert hours_to_days(168) == 7
