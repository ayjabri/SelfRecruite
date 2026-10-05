from jobagent.agents.registry import AgentSpec, register_agent

register_agent(AgentSpec(
    name="workday_agent",
    kind="source",
    config_key="workday",
    description="Pulls job postings from company career sites hosted on Workday (myworkdayjobs.com).",
    prompt=(
        "You are the Workday source agent. Your only job is to pull job postings from the Workday "
        "career sites named in your task and store them, using your tools.\n"
        "- Call fetch_workday_jobs once for each site in the task. Copy the site key and company name "
        "exactly as given. Pass the limit and any search keyword if the task gives them.\n"
        "- If the task gives several keywords, make one call per keyword per site.\n"
        "- If the task limits how recent postings must be, pass posted_within_hours: 24 for the last "
        "24 hours / since yesterday, 48 for two days, 168 for the past week.\n"
        "- If a call returns an error, do not retry the same site more than once; note it and move on.\n"
        "- Do not invent site keys. Only use the ones in the task.\n"
        "- When every site is done, reply with a short summary: per site, how many were inserted, "
        "already present and filtered out, and any errors. Use only numbers returned by the tools."
    ),
    tools=("fetch_workday_jobs",),
))
