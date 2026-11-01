# Contributing

Thanks for looking. Issues and pull requests are welcome.

## Setup

```bash
git clone https://github.com/ayjabri/SelfRecruite.git && cd SelfRecruite
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
docker compose up -d db
alembic upgrade head
pytest -q
```

## Conventions

- **Every capability is a tool.** Add a function under `jobagent/tools/` with Pydantic input and
  output models and register it with `@tool_spec`. It becomes callable from an agent, the CLI
  (`jobagent run <name>`) and the API with no further wiring.
- **Agents are one file each.** A new source, crawl stage or job agent is an `AgentSpec` under
  `jobagent/agents/` naming the tools it may call.
- **Schema changes go through Alembic.** Edit `jobagent/models.py`, then
  `alembic revision --autogenerate -m "..."`, review the file, and make sure `alembic check` is
  clean afterwards.
- **The web page has no build step.** Vanilla HTML, JS and CSS under `jobagent/web/`, every script
  a file under `static/` (the CSP forbids inline scripts). All page scripts share one global scope;
  `tests/test_page_scripts.py` parses them together, so a syntax error in one file fails the suite.
- **Tests run offline by default.** Anything that needs the database, the network or Ollama is
  marked `@pytest.mark.live` and runs with `pytest -m live`.
- Keep user data out of the repository: resumes, `.env`, `data/` and `backups/` are ignored.

## Pull requests

Describe what changed and why, keep the diff focused, and run `pytest -q` before opening it.
