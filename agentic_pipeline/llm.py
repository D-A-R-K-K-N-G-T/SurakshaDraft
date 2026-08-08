"""
Thin factory around the chat model.
Nothing here runs at import time. build_models() is called once, after the 
caller's config is active — so importing this package never requires an API 
key to be set or a provider's SDK to be importable, only *running* the pipeline does.
"""
from __future__ import annotations

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
    """
    last_error: Exception | None = None
    for _ in range(max_retries):
        try:
            return structured_model.invoke(messages, config=config)
        except Exception as e:
            last_error = e
    raise last_error