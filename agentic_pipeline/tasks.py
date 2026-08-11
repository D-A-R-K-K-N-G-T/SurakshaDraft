import logging
import traceback
from datetime import datetime
from agentic_pipeline.worker import celery_app
from agentic_pipeline.db import session_scope
from agentic_pipeline import repository as repo
from agentic_pipeline.state import ClaimState
from agentic_pipeline.graph import graph

_log = logging.getLogger("agentic_pipeline.tasks")

@celery_app.task
def run_pipeline_task(claim_ref: str, run_id: str, state_dict: dict):
    from agentic_pipeline import llm
    token = llm.set_run_context(run_id)
    try:
        with session_scope() as s:
            final = graph.invoke(state_dict)
            final_state = ClaimState.model_validate(final)
            repo.persist_success(s, run_id, final_state)
    except Exception as e:
        _log.exception("Claim pipeline failed for %s", claim_ref)
        try:
            with session_scope() as s:
                repo.persist_failure(s, run_id, str(e), traceback.format_exc())
        except Exception:
            _log.exception("Could not persist failure for %s", claim_ref)
    finally:
        llm.reset_run_context(token)

@celery_app.task
def reap_stale_runs():
    """Unlocks claims that have been stuck 'running' for over 1 hour."""
    _log.info("Reaping stale runs...")
    with session_scope() as s:
        count = repo.reap_stale_runs(s)
        _log.info(f"Reaped {count} stale runs.")

@celery_app.task
def drain_outbox():
    """Polls the outbox and mocks sending to the insurer intake API."""
    _log.info("Draining outbox...")
    with session_scope() as s:
        count = repo.drain_outbox(s)
        if count > 0:
            _log.info(f"Drained {count} outbox messages.")

@celery_app.task
def gc_expired_claims():
    """Deletes rows and blobs for claims past retention_expires_at."""
    _log.info("GC expired claims...")
    with session_scope() as s:
        count = repo.gc_expired_claims(s)
        if count > 0:
            _log.info(f"Garbage collected {count} expired claims.")
