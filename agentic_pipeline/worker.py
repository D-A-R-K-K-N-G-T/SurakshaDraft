"""
Celery worker and periodic tasks for SurakshaDraft.
"""
from celery import Celery
from agentic_pipeline.config import settings

celery_app = Celery(
    "agentic_pipeline",
    broker=settings.redis_url,
    include=["agentic_pipeline.tasks"]
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    beat_schedule={
        "reap-stale-runs": {
            "task": "agentic_pipeline.tasks.reap_stale_runs",
            "schedule": 3600.0, # Every hour
        },
        "drain-outbox": {
            "task": "agentic_pipeline.tasks.drain_outbox",
            "schedule": 60.0, # Every minute
        },
        "gc-expired-claims": {
            "task": "agentic_pipeline.tasks.gc_expired_claims",
            "schedule": 86400.0, # Daily
        }
    }
)
