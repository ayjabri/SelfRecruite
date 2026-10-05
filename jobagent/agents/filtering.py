from jobagent.agents.registry import AgentSpec, register_agent

register_agent(AgentSpec(
    name="filter_agent",
    kind="stage",
    order=5,
    description="Drops stored postings outside the target job titles or outside the US, before extraction.",
    default_task="Apply the pre-filter to every stored posting that has not been checked yet.",
    prompt=(
        "You are the pre-filter agent. You decide nothing yourself: the rules are in a config file and "
        "your tool applies them.\n"
        "1. Call filter_pending_postings once. Set recheck to true only if the task says the filter "
        "rules changed. Never set dry_run unless the task asks for a preview.\n"
        "2. Reply with a short summary: how many were checked, kept and filtered, and the count for "
        "each filter reason. Use only numbers returned by the tool."
    ),
    tools=("filter_pending_postings",),
))
