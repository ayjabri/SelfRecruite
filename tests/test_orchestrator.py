from jobagent.agents.orchestrator import attach_targets

WORKDAY = [{"company": "Citi", "site": "citi:wd5:2"}, {"company": "U.S. Bank", "site": "usbank:wd1:US_Bank_Careers"},
           {"company": "MUFG", "site": "mufgub:wd3:MUFG-Careers"}, {"company": "Ally", "site": "ally:wd1:Ally"}]


def test_named_targets_are_attached_with_their_keys():
    task = attach_targets("Pull new job postings from US Bank and MUFG posted in the past week.", WORKDAY)
    assert "usbank:wd1:US_Bank_Careers" in task and "mufgub:wd3:MUFG-Careers" in task
    assert "citi:wd5:2" not in task
    assert "ally:wd1:Ally" not in attach_targets("Finally, pull MUFG.", WORKDAY)   # whole words only


def test_all_targets_when_the_task_names_none():
    task = attach_targets("Pull at most 100 new postings per target.", WORKDAY)
    assert all(t["site"] in task for t in WORKDAY) and "all of them" in task


def test_plain_string_targets_and_no_targets():
    assert '"anthropic"' in attach_targets("Pull Anthropic.", ["anthropic", "stripe"])
    assert '"stripe"' not in attach_targets("Pull Anthropic.", ["anthropic", "stripe"])
    assert attach_targets("Pull.", []) == "Pull."
