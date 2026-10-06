"""One place that builds the chat model.

The shared default comes from LLM_MODEL in .env ("<provider>:<model>", e.g. ollama:qwen3.6:27b).
A user may connect their own Claude or OpenAI account in their profile (`llm` section); then
`get_chat_model(user_id=...)` uses that provider, model and key for their LLM work.
"""
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from jobagent.config import get_settings

OWN_PROVIDERS = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-5"}  # provider -> default model


def user_llm(user_id: int | None) -> dict | None:
    """{'provider', 'model', 'api_key'} when the user connected their own account, else None."""
    if user_id is None:
        return None
    from jobagent.secrets import decrypt
    from jobagent.users import get_profile
    llm = get_profile(user_id)["llm"] or {}
    provider = llm.get("provider")
    if provider not in OWN_PROVIDERS or not llm.get("api_key_enc"):
        return None
    return {"provider": provider, "model": llm.get("model") or OWN_PROVIDERS[provider],
            "api_key": decrypt(llm["api_key_enc"])}


def model_spec(user_id: int | None = None) -> str:
    """"<provider>:<model>" that get_chat_model would use for this user (for records, no key)."""
    own = user_llm(user_id)
    return f"{own['provider']}:{own['model']}" if own else get_settings().llm_model


def get_chat_model(model: str | None = None, user_id: int | None = None) -> BaseChatModel:
    """`model` is "<provider>:<model>"; else the user's own account; else LLM_MODEL."""
    s = get_settings()
    if model is None and (own := user_llm(user_id)):
        return init_chat_model(own["model"], model_provider=own["provider"], temperature=0, api_key=own["api_key"])
    spec = model or s.llm_model
    provider, _, name = spec.partition(":")
    if provider == "ollama":
        return init_chat_model(
            name, model_provider="ollama", base_url=s.ollama_base_url,
            temperature=0, num_ctx=s.ollama_num_ctx, reasoning=False,
        )
    return init_chat_model(name, model_provider=provider, temperature=0)
