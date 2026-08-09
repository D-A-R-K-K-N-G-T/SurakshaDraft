"""
Central configuration for the SurakshaDraft agentic pipeline.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- persistence (Phase 1: database foundation) ---
    # SQLAlchemy URL for the pipeline's Postgres. The default matches
    # docker-compose.yml so `docker compose up -d db` + alembic just works
    # locally; override via DATABASE_URL in .env / the environment. Always use
    # the psycopg (v3) driver — psycopg2 is not a dependency of this project.
    database_url: str = (
        "postgresql+psycopg://suraksha:suraksha@localhost:5432/suraksha"
    )
    # Echo emitted SQL to the logger. Handy while wiring the repository layer;
    # keep off in normal runs and never on in production (it logs query params).
    database_echo: bool = False

    # Where requirements.load_ruleset reads master LORs from:
    #   "file" — the JSON files under agentic_pipeline/rulesets/ (default; keeps
    #            local dev and the pure-function tests working with zero DB).
    #   "db"   — the rulesets catalogue tables (Phase 2). Fail-open either way:
    #            a load error logs and returns None, never blocks a claim.
    ruleset_source: str = "file"

    # --- content-addressed blob store (Phase 3) ---
    # Root of the content-addressed layout: uploads/{sha[:2]}/{sha}{ext}. The
    # gateway writes here and emits fs:// URIs; the backfill script reorganizes
    # legacy uploads into it. Relative paths resolve from the gateway CWD
    # (backend/) — the app and pipeline share this filesystem in dev.
    blob_store_root: str = "backend/uploads"

    # --- S3 Blob Store ---
    # If s3_bucket is set, the pipeline uses S3 instead of local fs.
    s3_bucket: str = ""
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"

    # --- PII Encryption ---
    # 32-byte hex string for AES-GCM envelope encryption
    pii_master_key: str = ""

    # --- Task Queue ---
    redis_url: str = "redis://localhost:6379/0"

    # --- draft-pack output (Phase 5) ---
    # The DB is now the system of record for draft packs (draft_packs table);
    # the legacy ./out/claim_pack_{hash}.json dump is optional local-debug only.
    write_pack_files: bool = False

    # --- identity (Phase 7) ---
    # "disabled" — no identity; claims stay anonymous (default, non-breaking).
    # "demo"     — the bearer token IS the user subject (dev/testing only).
    # "firebase" — verify a Firebase ID token via firebase-admin (needs
    #              GOOGLE_APPLICATION_CREDENTIALS / a service account).
    auth_mode: str = "firebase"

    # --- observability (Phase 8) ---
    # When True, invoke_structured records each call into llm_invocations. Off by
    # default (zero overhead / no behavior change); enable in deployments that
    # want the LLM audit trail.
    record_llm_invocations: bool = False

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