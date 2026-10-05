from jobagent.parse.core import (
    label_heading, parse_compensation, parse_locations, parse_min_years, parse_posting, parse_sections,
    parse_visa, parse_workplace,
)
from jobagent.tools.common import NormalizedPosting

HTML = """
<h3>About the role</h3><p>We build things.</p>
<p><strong>What you'll do</strong></p><ul><li>Own the roadmap</li><li>Ship features</li></ul>
<h3>Minimum requirements</h3><ul><li>10+ years of experience, including 3+ years managing teams</li>
<li>Python</li></ul>
<p>Preferred qualifications:</p><ul><li>MBA</li><li>15 years in banking</li></ul>
<p>The annual salary range is $150,000 - $200,000. We do not provide visa sponsorship for this role.</p>
"""


def test_sections_are_split_and_labelled_by_heading():
    s = {x.label: x for x in parse_sections(HTML)}
    assert s["responsibilities"].bullets == ["Own the roadmap", "Ship features"]
    assert s["required"].heading == "Minimum requirements" and len(s["required"].bullets) == 2
    assert s["preferred"].bullets == ["MBA", "15 years in banking"]


def test_heading_labels():
    assert label_heading("Preferred qualifications, capabilities, and skills") == "preferred"
    assert label_heading("Required qualifications, capabilities, and skills") == "required"
    assert label_heading("What we look for") == "required"
    assert label_heading("Ways to stand out from the crowd") == "preferred"
    assert label_heading("The impact you will have") == "responsibilities"
    assert label_heading("Benefits") == "other"


def test_compensation_patterns():
    assert parse_compensation("Pay: $150,000 - $200,000 a year.")[:3] == (150000, 200000, "year")
    # no dollar sign, several levels merged
    text = "The base salary range is 168,000 USD - 264,500 USD for Level 4, and 200,000 USD - 310,500 USD for Level 5."
    assert parse_compensation(text)[:3] == (168000, 310500, "year")
    assert parse_compensation("Range: $120K - $150K")[:2] == (120000, 150000)
    assert parse_compensation("The hourly rate is 20 USD - 56 USD.")[:3] == (20, 56, "hour")
    assert parse_compensation("You have 3 - 5 years of experience and manage $2 - $3 billion")[0] is None
    assert parse_compensation("No numbers here")[0] is None


def test_years_takes_the_headline_figure_from_requirements():
    assert parse_min_years(["10+ years of experience, including 3+ years managing teams"], "") == 10
    assert parse_min_years([], "Founded 150 years ago. Requires 5 years of experience; 8 preferred.") == 5
    assert parse_min_years([], "no figures") is None


def test_workplace_and_visa():
    assert parse_workplace("Remote-Friendly, United States", "") == "remote"
    assert parse_workplace("New York, NY", "This is a hybrid role.") == "hybrid"
    assert parse_workplace("New York, NY", "Our remote work policy is generous.") == "unknown"
    assert parse_visa("We do not provide visa sponsorship for this role.") == "no"
    assert parse_visa("Visa sponsorship is available.") == "yes"
    assert parse_visa("Great team.") == "unstated"


def test_locations():
    locs = [(x.city, x.state, x.country) for x in parse_locations("San Francisco, CA | New York City, NY")]
    assert locs == [("San Francisco", "CA", "US"), ("New York City", "NY", "US")]
    assert [(x.city, x.state, x.country) for x in parse_locations("US, CA, Santa Clara")] == [("Santa Clara", "CA", "US")]
    assert [(x.city, x.state, x.country) for x in parse_locations("Tokyo, Japan")] == [("Tokyo", None, "Japan")]
    assert [(x.city, x.state, x.country) for x in parse_locations("Charlotte, NC, United States")] == [("Charlotte", "NC", "US")]
    # Citi's Workday format has no commas
    assert [(x.city, x.state) for x in parse_locations("Tampa Florida United States; Getzville New York United States")] == [("Tampa", "FL"), ("Getzville", "NY")]


def test_parse_posting_end_to_end():
    p = NormalizedPosting(company="Acme", title="Senior Treasury Analyst", location_text="New York, NY",
                          description_html=HTML, description_text="The annual salary range is $150,000 - $200,000. "
                          "We do not provide visa sponsorship for this role.", employment_type="Full time")
    j = parse_posting(p)
    assert j.sections_quality == "labelled"
    assert (j.comp_min, j.comp_max, j.comp_period) == (150000, 200000, "year")
    assert j.min_years_experience == 10 and j.seniority == "senior"
    assert j.employment_type == "full_time" and j.visa_sponsorship == "no"
    assert j.requirements == ["10+ years of experience, including 3+ years managing teams", "Python"]


def test_unlabelled_bullets_all_count_as_requirements():
    p = NormalizedPosting(company="A", title="Analyst", description_html="<ul><li>One</li><li>Two</li></ul>")
    j = parse_posting(p)
    assert j.sections_quality == "unlabelled" and j.requirements == ["One", "Two"]


def test_typed_bullets_and_uppercase_headings():
    html = ("<p>Intro text.</p><p>KEY RESPONSIBILITIES</p><p>• Own delivery</p><p>• Manage budgets</p>"
            "<p>REQUIRED QUALIFICATIONS</p><p>- 10+ years in banking</p><p>Closing paragraph.</p>")
    s = {x.label: x.bullets for x in parse_sections(html)}
    assert s == {"responsibilities": ["Own delivery", "Manage budgets"], "required": ["10+ years in banking"]}
