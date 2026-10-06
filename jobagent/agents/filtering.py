from jobagent.agents.registry import AgentSpec, register_agent

register_agent(AgentSpec(
    name="filter_agent",
    kind="stage",
    order=5,
    description="Drops stored postings outside the target job titles, outside the US, or outside the allowed states (NY/NJ unless the request says otherwise), before parsing.",
    default_task="Apply the pre-filter to every stored posting that has not been checked yet.",
    prompt=(
        "You are the pre-filter agent. You decide nothing yourself: the rules are in a config file and "
        "your tool applies them.\n"
        "1. Call filter_pending_postings once. Pass states (e.g. [\"NY\", \"NJ\"]) only if the task names states. Set recheck to true only if the task says the filter "
        "rules changed. Never set dry_run unless the task asks for a preview.\n"
        "2. Reply with a short summary: how many were checked, kept and filtered, and the count for "
        "each filter reason. Use only numbers returned by the tool."
    ),
    tools=("filter_pending_postings",),
    direct=True,
))
