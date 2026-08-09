"""
Thin factory around the chat model.
Nothing here runs at import time. build_models() is called once, after the 
caller's config is active — so importing this package never requires an API 
key to be set or a provider's SDK to be importable, only *running* the pipeline does.
"""
from __future__ import annotations

import warnings
import logging

warnings.filterwarnings("ignore", message=".*\\$defs.*")

class SuppressDefsWarning(logging.Filter):
    def filter(self, record):
        return "'$defs' is not supported" not in record.getMessage()

logging.getLogger("langchain_core.utils.json_schema").addFilter(SuppressDefsWarning())
logging.getLogger("langchain_google_genai.chat_models").addFilter(SuppressDefsWarning())
# Catch-all for the root logger just in case
logging.getLogger().addFilter(SuppressDefsWarning())

from dataclasses import dataclass
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig
from agentic_pipeline.config import settings


@dataclass
class Models:
    default: Any
    vision: Any


_MODELS: Optional[Models] = None


def _build_one(
    provider: str,
    model: str,
    temperature: float,
    **kwargs
):
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        if settings.google_api_key:
            kwargs["google_api_key"] = settings.google_api_key
        return ChatGoogleGenerativeAI(model=model, temperature=temperature, **kwargs)
        
    if provider == "watsonx":
        from langchain_ibm import ChatWatsonx
        return ChatWatsonx(
            model_id=model,
            url=settings.watsonx_url,
            project_id=settings.watsonx_project_id,
            api_key=settings.watsonx_api_key or None,
            params={"temperature": temperature},
        )
        
    raise ValueError(f"Unknown LLM provider: {provider!r} (expected 'gemini' or 'watsonx')")


def build_models() -> Models:
    global _MODELS
    if _MODELS is not None:
        return _MODELS
        
    provider = settings.llm_provider
    temp = settings.default_temperature
    
    default_model_name = settings.gemini_default_model if provider == "gemini" else settings.watsonx_default_model
    vision_model_name = settings.gemini_vision_model if provider == "gemini" else settings.watsonx_vision_model
    if vision_model_name is None:
        vision_model_name = default_model_name
        
    _MODELS = Models(
        default=_build_one(
            provider=provider,
            model=default_model_name,
            temperature=temp,
        ),
        vision=_build_one(
            provider=provider,
            model=vision_model_name,
            temperature=temp,
        )
    )
    return _MODELS


def get_structured_llm(
    schema,
    *,
    want_vision: bool = False,
    method: str = "function_calling",
):
    models = _MODELS or build_models()
    base_llm = models.vision if want_vision else models.default
    return base_llm.with_structured_output(schema, method=method)


_log = logging.getLogger("agentic_pipeline.llm")

# Run context for the llm_invocations recorder (Phase 8). The background pipeline
# task sets this so recorded calls carry their run_id without changing any node
# signature. Off unless settings.record_llm_invocations is True.
import contextvars
import time

run_context: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar(
    "llm_run_context", default=None
)


def set_run_context(run_id):
    return run_context.set({"run_id": run_id})


def reset_run_context(token) -> None:
    try:
        run_context.reset(token)
    except Exception:
        pass


def _record_invocation(succeeded: bool, latency_ms: int, error_type: str | None) -> None:
    if not settings.record_llm_invocations:
        return
    try:
        from agentic_pipeline.db import session_scope
        from agentic_pipeline import repository as repo

        ctx = run_context.get() or {}
        provider = settings.llm_provider
        model = (settings.gemini_default_model if provider == "gemini"
                 else settings.watsonx_default_model)
        with session_scope() as s:
            repo.record_llm_invocation(
                s, provider=provider, model=model, succeeded=succeeded,
                run_id=ctx.get("run_id"), latency_ms=latency_ms, error_type=error_type,
            )
    except Exception:
        _log.exception("Could not record llm invocation")


def invoke_structured(
    structured_model,
    messages: list,
    config: RunnableConfig | None = None,
    max_retries: int = 3,
):
    """Invoke a `.with_structured_output(...)` model with retries.

    Some providers enforce structured output natively (native tool
    calling); others fall back to prompted JSON and occasionally return
    a payload that fails schema validation. Retry a few times — a fresh
    sample often self-corrects — before re-raising the last error.

    Every failure is logged (attempt number, exception type, message) so
    schema-validation drift and transient provider errors are visible
    rather than silently swallowed.
    """
    if max_retries < 1:
        raise ValueError(f"max_retries must be >= 1, got {max_retries}")

    last_error: Exception | None = None
    started = time.monotonic()
    for attempt in range(1, max_retries + 1):
        try:
            result = structured_model.invoke(messages, config=config)
            _record_invocation(True, int((time.monotonic() - started) * 1000), None)
            return result
        except Exception as e:
            last_error = e
            _log.warning(
                "invoke_structured attempt %d/%d failed: %s: %s",
                attempt, max_retries, type(e).__name__, e,
            )
    _record_invocation(False, int((time.monotonic() - started) * 1000),
                       type(last_error).__name__ if last_error else None)
    _log.error(
        "invoke_structured exhausted %d attempts; re-raising last error: %s: %s",
        max_retries, type(last_error).__name__, last_error,
    )
    raise last_error