from jobagent.agents.registry import AgentSpec, register_agent

register_agent(AgentSpec(
    name="parse_agent",
    kind="stage",
    order=10,
    description="Turns stored postings that passed the pre-filter into structured job records, using rules only.",
    default_task="Parse every kept posting that has no job yet.",
    prompt=(
        "You are the parse agent. Call parse_pending_postings once, then reply with a short summary of "
        "the counts it returned. Use only numbers returned by the tool."
    ),
    tools=("parse_pending_postings",),
    direct=True,
))
