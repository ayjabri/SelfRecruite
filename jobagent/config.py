"""Settings, read once from the environment / .env."""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str
    # "<provider>:<model>", e.g. "ollama:qwen3.6:27b" or "anthropic:claude-sonnet-5-5"
    llm_model: str
    ollama_base_url: str
    ollama_num_ctx: int
    companies_file: Path

    @property
    def psycopg_url(self) -> str:
        """Plain libpq URL (no SQLAlchemy driver suffix), for the LangGraph checkpointer."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        llm_model=os.environ.get("LLM_MODEL", "ollama:qwen3.6:27b"),
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_num_ctx=int(os.environ.get("OLLAMA_NUM_CTX", "16384")),
        companies_file=Path(os.environ.get("COMPANIES_FILE", ROOT / "config" / "companies.yaml")),
    )
