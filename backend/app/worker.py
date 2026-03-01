from app.celery_app import celery_app
from app.config import get_settings
from app.db import SessionLocal
from app.logging_utils import get_logger, sanitize_log_context
from app.models import SyncRun
from app.services.ingestion import (
    compact_error_code,
    mark_job_retrying,
    mark_job_terminal_failure,
    process_ingestion_job,
)
from app.services.map_reduce import build_map_reduce_summary
from app.services.sync_run import execute_sync_run

settings = get_settings()
logger = get_logger(__name__)


@celery_app.task(bind=True, name="fkv.ingestion.process_job")
def process_ingestion_job_task(self, job_id: str, force_reprocess: bool = False, reprocess_doc_id: str | None = None):
    try:
        return process_ingestion_job(job_id, force_reprocess=force_reprocess, reprocess_doc_id=reprocess_doc_id)
    except Exception as exc:
        max_retries = max(0, int(settings.ingestion_retry_max_retries))
        current_retry = int(getattr(self.request, "retries", 0))
        error_code = compact_error_code(f"worker_exception:{type(exc).__name__}")

        if current_retry < max_retries:
            retry_count = current_retry + 1
            mark_job_retrying(job_id, error_code=error_code, retry_count=retry_count, max_retries=max_retries)
            base_delay = max(1, int(settings.ingestion_retry_base_delay_sec))
            countdown = base_delay * (2 ** current_retry)
            raise self.retry(exc=exc, countdown=countdown, max_retries=max_retries)

        mark_job_terminal_failure(job_id, error_code=f"retry_exhausted:{error_code}")
        raise


@celery_app.task(bind=True, name="fkv.sync.execute_run")
def execute_sync_run_task(
    self,
    run_id: str,
    nas_paths: list[str] | None = None,
    recursive: bool = True,
    mail_max_results: int | None = None,
):
    db = SessionLocal()
    try:
        execute_sync_run(
            db,
            run_id,
            nas_paths=nas_paths,
            recursive=bool(recursive),
            mail_max_results=mail_max_results,
        )
        return {"run_id": str(run_id or ""), "status": "completed"}
    except Exception as exc:
        run = db.get(SyncRun, str(run_id or "").strip())
        if run is not None:
            run.status = "failed"
            run.error_code = compact_error_code(f"sync_run_failed:{type(exc).__name__}")
            db.commit()
        logger.warning(
            "sync_run_task_failed",
            extra=sanitize_log_context(
                {
                    "run_id": str(run_id or ""),
                    "error_code": compact_error_code(f"sync_run_failed:{type(exc).__name__}"),
                    "exc_type": type(exc).__name__,
                }
            ),
        )
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="fkv.map_reduce.process")
def run_map_reduce_task(self, doc_id: str, ui_lang: str = "zh", chunk_group_size: int = 6):
    """Async Celery task for long-document map-reduce summarisation.

    Persists intermediate page/section checkpoints to the DB so that if the
    HTTP request times out, completed work is not lost and can be resumed
    via the status endpoint.
    """
    db = SessionLocal()
    try:
        result = build_map_reduce_summary(db, doc_id=doc_id, ui_lang=ui_lang, chunk_group_size=chunk_group_size)
        return {
            "doc_id": doc_id,
            "status": "completed",
            "quality_state": str(result.quality_state or ""),
            "pages_total": int(result.pages_total or 0),
            "pages_used": int(result.pages_used or 0),
        }
    except ValueError as exc:
        logger.warning(
            "map_reduce_task_value_error",
            extra=sanitize_log_context({"doc_id": str(doc_id or ""), "error": str(exc)}),
        )
        return {"doc_id": doc_id, "status": "failed", "error": str(exc)}
    except Exception as exc:
        logger.warning(
            "map_reduce_task_failed",
            extra=sanitize_log_context({"doc_id": str(doc_id or ""), "exc_type": type(exc).__name__}),
        )
        raise
    finally:
        db.close()
