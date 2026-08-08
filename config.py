"""
Central configuration for the SurakshaDraft agentic pipeline.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- provider switch ---
    # "gemini" while prototyping in the notebook, "watsonx" for the real deployment.
    llm_provider: str = "gemini"

    # --- Gemini (langchain-google-genai) — notebook testing ---
    google_api_key: str = ""
    gemini_default_model: str = "gemini-pro"
    gemini_vision_model: str | None = None  # falls back to gemini_default_model if unset

    # --- watsonx (langchain-ibm) — production ---
    watsonx_api_key: str = ""
    watsonx_url: str = "https://us-south.ml.cloud.ibm.com"
    watsonx_project_id: str = ""
    watsonx_default_model: str = "meta-llama/llama-3-3-70b-instruct"
    watsonx_vision_model: str = "meta-llama/llama-3-2-90b-vision-instruct"

    default_temperature: float = 0.0

    # --- evidence_verify thresholds (Phase C) ---
    geofence_radius_m: float = 150.0
    flood_window_hours_before: int = 6
    flood_window_hours_after: int = 240


settings = Settings()