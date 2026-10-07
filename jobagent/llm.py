"""One place that builds the chat model.

Two providers for users: the shared local Ollama server (free; LLM_MODEL in .env) and Claude
with the user's own API key (profile `llm` section: provider, model, encrypted key). OpenAI is
kept as a code path but not offered on the page.
"""
import time
from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from jobagent.config import get_settings

LOCAL = "local"
OWN_PROVIDERS = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-5"}  # provider -> default model
# shown when Anthropic's model list cannot be fetched
ANTHROPIC_FALLBACK = ["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5-20251001"]


class LLMError(RuntimeError):
    pass


def provider_of(llm: dict | None) -> str:
    p = (llm or {}).get("provider") or LOCAL
    return LOCAL if p in (LOCAL, "shared") else p


def user_llm(user_id: int | None) -> dict | None:
    """{'provider', 'model', 'api_key'} when the user connected their own account, else None."""
    if user_id is None:
        return None
    from jobagent.secrets import decrypt
    from jobagent.users import get_profile
    llm = get_profile(user_id)["llm"] or {}
    provider = provider_of(llm)
    if provider not in OWN_PROVIDERS or not llm.get("api_key_enc"):
        return None
    return {"provider": provider, "model": llm.get("model") or OWN_PROVIDERS[provider],
            "api_key": decrypt(llm["api_key_enc"])}


def model_spec(user_id: int | None = None) -> str:
    """"<provider>:<model>" that get_chat_model would use for this user (for records, no key)."""
    own = user_llm(user_id)
    return f"{own['provider']}:{own['model']}" if own else get_settings().llm_model


def uses_shared_model(user_id: int | None) -> bool:
    return user_llm(user_id) is None


def get_chat_model(model: str | None = None, user_id: int | None = None) -> BaseChatModel:
    """`model` is "<provider>:<model>"; else the user's own account; else LLM_MODEL."""
    s = get_settings()
    if model is None and (own := user_llm(user_id)):
        # newer Claude models refuse a non-default temperature; leave it unset for them
        return init_chat_model(own["model"], model_provider=own["provider"], api_key=own["api_key"], **_sampling(own["provider"]))
    spec = model or s.llm_model
    provider, _, name = spec.partition(":")
    if provider == "ollama":
        return init_chat_model(
            name, model_provider="ollama", base_url=s.ollama_base_url,
            temperature=0, num_ctx=s.ollama_num_ctx, reasoning=False,
        )
    return init_chat_model(name, model_provider=provider, **_sampling(provider))


def _sampling(provider: str) -> dict:
    """temperature=0 where the provider allows it; nothing for Anthropic (Claude 5 rejects it)."""
    return {} if provider == "anthropic" else {"temperature": 0}


# --- Anthropic model list and key test -----------------------------------------------------

def _anthropic_models(api_key: str) -> list[dict]:
    """Chat models this key can use, newest first. Raises LLMError with the provider's message."""
    import anthropic
    try:
        client = anthropic.Anthropic(api_key=api_key)
        page = client.models.list(limit=100)
    except anthropic.AuthenticationError as e:
        raise LLMError("Anthropic rejected the key") from e
    except anthropic.APIError as e:
        raise LLMError(f"Anthropic: {getattr(e, 'message', str(e))[:200]}") from e
    except Exception as e:  # noqa: BLE001 - network, DNS...
        raise LLMError(f"could not reach Anthropic: {type(e).__name__}") from e
    models = [{"id": m.id, "name": getattr(m, "display_name", None) or m.id,
               "created_at": str(getattr(m, "created_at", "") or "")} for m in page.data]
    return sorted(models, key=lambda m: m["created_at"], reverse=True) or [{"id": i, "name": i, "created_at": ""} for i in ANTHROPIC_FALLBACK]


@lru_cache(maxsize=256)
def _cached_models(key_fingerprint: str, hour: int, api_key: str) -> tuple[list[dict], str | None]:
    try:
        return _anthropic_models(api_key), None
    except LLMError as e:
        return [{"id": i, "name": i, "created_at": ""} for i in ANTHROPIC_FALLBACK], str(e)


def available_models(user_id: int) -> dict:
    """{'provider', 'models': [...], 'current', 'error'} for the user's stored account; one hour cache."""
    own = user_llm(user_id)
    if own is None:
        return {"provider": LOCAL, "models": [{"id": get_settings().llm_model, "name": get_settings().llm_model + " (shared, free)"}],
                "current": get_settings().llm_model, "error": None}
    if own["provider"] != "anthropic":
        return {"provider": own["provider"], "models": [{"id": own["model"], "name": own["model"]}], "current": own["model"], "error": None}
    import hashlib
    fp = hashlib.sha256(own["api_key"].encode()).hexdigest()[:16]
    models, error = _cached_models(fp, int(time.time() // 3600), own["api_key"])
    return {"provider": "anthropic", "models": models, "current": own["model"], "error": error}


def test_key(provider: str, api_key: str) -> dict:
    """One cheap call with a key: {'ok', 'detail', 'models'}."""
    if provider != "anthropic":
        return {"ok": False, "detail": "only Claude keys can be tested for now", "models": []}
    if not api_key.startswith("sk-ant-") or len(api_key) < 40:
        return {"ok": False, "detail": "that is not an Anthropic API key (they start with sk-ant- and are long); check the field", "models": []}
    try:
        models = _anthropic_models(api_key)
    except LLMError as e:
        return {"ok": False, "detail": str(e), "models": []}
    return {"ok": True, "detail": f"key works; {len(models)} models available", "models": models}
