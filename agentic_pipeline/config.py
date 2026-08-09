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
    gemini_default_model: str = "gemini-3.1-flash-lite"
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

    # --- document triage gate ---
    # "enforce": a positively-identified wrong document fails intake.
    # "warn_only": the same finding is recorded as a warning and the claim
    # proceeds. Flip to warn_only (no code change) if real claims start getting
    # blocked; the coverage cap below is an independent safety net either way.
    doc_gate_mode: str = "enforce"
    # Minimum self-reported confidence before a wrong-document finding may block.
    doc_triage_mismatch_confidence: float = 0.80
    # Loss dates this close to a policy boundary get a warning, not a rejection.
    policy_period_boundary_tolerance_days: int = 1

    # --- LOR / requirements gate ---
    # The claimant never confirms the inferred claim type, so their checklist is
    # only allowed to BLOCK on a confident classification. Below this score, or
    # when the runner-up is within claim_type_margin, the LOR becomes the union
    # of the candidate sections with everything non-universal demoted to advisory.
    claim_type_min_confidence: float = 0.70
    claim_type_margin: float = 0.15
    # "enforce": missing blocking requirements halt the claim at awaiting_documents.
    # "warn_only": the checklist is still produced but never halts the pipeline.
    lor_gate_mode: str = "enforce"


settings = Settings()


# Flat depreciation rate applied per category when the valuation agent prices an
# item (net_loss = purchase_value * (1 - rate)). Keyed on the lowercased pipeline
# category; DEPRECIATION_DEFAULT is used for anything unmatched. These are
# placeholder rates — replace with the insurer's actual depreciation schedule.
DEPRECIATION_DEFAULT = 0.10
DEPRECIATION_BY_CATEGORY = {
    "stock": 0.0,                              # trading stock is not depreciated
    "furniture, fixtures & fittings": 0.10,
    "plant & machinery": 0.15,
    "electronics": 0.25,
    "vehicle": 0.15,
    "valuables": 0.0,
    "property": 0.05,
}