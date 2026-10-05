from jobagent.agents.registry import AgentSpec, register_agent

register_agent(AgentSpec(
    name="greenhouse_agent",
    kind="source",
    config_key="greenhouse",
    description="Pulls job postings from Greenhouse job boards, one board slug per company.",
    prompt=(
        "You are the Greenhouse source agent. Your only job is to pull job postings from the "
        "Greenhouse boards named in your task and store them, using your tools.\n"
        "- Call fetch_greenhouse_jobs once for each board in the task. Pass the limit if the task gives one.\n"
        "- If a call returns an error, do not retry the same board more than once; note it and move on.\n"
        "- Do not invent board names. Only use the ones in the task.\n"
        "- When every board is done, reply with a short summary: per board, how many were fetched, "
        "inserted and already present, and any errors. Use only numbers returned by the tools."
    ),
    tools=("fetch_greenhouse_jobs",),
))
