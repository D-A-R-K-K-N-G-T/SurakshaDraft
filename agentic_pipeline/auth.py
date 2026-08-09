"""Pluggable identity resolution (Phase 7).

`settings.auth_mode` selects the backend:
  * disabled — no identity; claims stay anonymous (default, non-breaking).
  * demo     — the bearer token IS the user subject (dev/testing only).
  * firebase — verify a Firebase ID token via firebase-admin (lazy import).

Everything here FAILS OPEN: any verification or DB error returns None, so an
auth hiccup never blocks a flood claimant — the claim just stays anonymous.
"""
from __future__ import annotations

import logging
from typing import Optional

from agentic_pipeline.config import settings

_log = logging.getLogger(__name__)
_firebase_ready = False


def bearer_token(authorization: Optional[str]) -> Optional[str]:
    """Extract the token from an 'Authorization: Bearer <token>' header."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


def _ensure_firebase() -> None:
    global _firebase_ready
    if _firebase_ready:
        return
    import firebase_admin  # lazy: not a hard dependency in dev/tests

    if not firebase_admin._apps:
        # Uses GOOGLE_APPLICATION_CREDENTIALS / ADC.
        firebase_admin.initialize_app()
    _firebase_ready = True


def verify_token(id_token: Optional[str]) -> Optional[dict]:
    """Return {provider, subject, email, name, photo} or None."""
    if not id_token:
        return None
    mode = settings.auth_mode
    if mode == "disabled":
        return None
    if mode == "demo":
        return {"provider": "demo", "subject": id_token, "email": None, "name": None, "photo": None}
    if mode == "firebase":
        try:
            _ensure_firebase()
            from firebase_admin import auth as fb_auth

            decoded = fb_auth.verify_id_token(id_token)
            return {
                "provider": "firebase",
                "subject": decoded["uid"],
                "email": decoded.get("email"),
                "name": decoded.get("name"),
                "photo": decoded.get("picture"),
            }
        except Exception:
            _log.exception("Firebase token verification failed")
            return None
    _log.warning("Unknown auth_mode %r; treating request as anonymous", mode)
    return None


def resolve_user(session, id_token: Optional[str]) -> Optional[str]:
    """Verify a token and get-or-create the users row, returning its id (or None)."""
    claims = verify_token(id_token)
    if not claims:
        return None
    try:
        from agentic_pipeline import repository as repo

        return repo.upsert_user(
            session,
            provider=claims["provider"],
            subject=claims["subject"],
            email=claims.get("email"),
            name=claims.get("name"),
            photo=claims.get("photo"),
        )
    except Exception:
        _log.exception("Could not resolve/create user")
        return None
