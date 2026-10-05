"""One place that builds the chat model, so the provider can be swapped in .env."""
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from jobagent.config import get_settings


def get_chat_model(model: str | None = None) -> BaseChatModel:
    """`model` is "<provider>:<model>"; defaults to LLM_MODEL."""
    s = get_settings()
    spec = model or s.llm_model
    provider, _, name = spec.partition(":")
    if provider == "ollama":
        return init_chat_model(
            name, model_provider="ollama", base_url=s.ollama_base_url,
            temperature=0, num_ctx=s.ollama_num_ctx, reasoning=False,
        )
    return init_chat_model(name, model_provider=provider, temperature=0)
