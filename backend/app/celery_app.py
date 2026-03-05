from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "family_vault", broker=settings.redis_url, backend=settings.redis_url
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_always_eager=settings.celery_task_always_eager,
)
