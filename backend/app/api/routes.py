import datetime as dt
import json
import mimetypes
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import crud
from app.api.auth_routes import router as auth_router
from app.api.deps import get_current_user, get_db
from app.auth import (
    COOKIE_NAME,
    create_access_token,
    is_setup_complete,
    set_admin_password,
    verify_admin_password,
)
from app.celery_app import celery_app
from app.config import get_settings
from app.logging_utils import get_logger, sanitize_log_context
from app.models import (
    AppSetting,
    Chunk,
    Document,
    DocumentStatus,
    IngestionJob,
    IngestionJobStatus,
    MailIngestionEvent,
    SyncRun,
    SyncRunItem,
)
from app.schemas import (
    AgentExecuteRequest,
    AgentExecuteResponse,
    BilingualText,
    CategoriesResponse,
    CategoryItem,
    DocumentChunk,
    DocumentContentAvailabilityResponse,
    DocumentListItem,
    DocumentListResponse,
    DocumentResponse,
    DocumentTagItem,
    DocumentTagsPatchRequest,
    DocumentTagsResponse,
    FriendlyNameResponse,
    FriendlyNameUpdateRequest,
    GovernanceCategoryDebtResponse,
    GovernanceCategoryDebtTrendPoint,
    GovernanceCategoryDebtTrendResponse,
    HealthResponse,
    IngestionJobCreateRequest,
    IngestionJobDeleteResponse,
    IngestionJobResponse,
    MailEventItem,
    MailEventsResponse,
    MailHealthResponse,
    MailPollRequest,
    MailPollResponse,
    MapReduceSummaryRequest,
    MapReduceSummaryResponse,
    NasScanRequest,
    NasScanResponse,
    PlannerDecision,
    PlannerRequest,
    QueueDocumentItem,
    QueueJobItem,
    QueueResponse,
    ReprocessResponse,
    SearchRequest,
    SearchResponse,
    SyncLastResponse,
    SyncRunDetailResponse,
    SyncRunItemResponse,
    SyncRunStartRequest,
    SyncRunStartResponse,
    SyncRunSummary,
    SyncSourceSummary,
    SystemPromptsResponse,
    TagCatalogItem,
    TagCatalogResponse,
    TaskCreateRequest,
    TaskListItem,
    TaskListResponse,
    TaskResponse,
)
from app.services.agent import execute_agent
from app.services.agent_graph import stream_agent_graph
from app.services.document_post_process import (
    apply_summary_to_doc,
    load_chunk_excerpt,
    recompute_category,
    recompute_name_and_facts,
    recompute_tags,
    sync_to_qdrant,
)
from app.services.governance import (
    build_category_debt_snapshot,
    compute_debt_trend,
    load_snapshots_from_dir,
)
from app.services.ingestion import enqueue_ingestion_job, parse_retry_meta
from app.services.llm_summary import (
    normalize_vehicle_insurance_summary,
    prompt_snapshot,
)
from app.services.mail_ingest import get_gmail_health, poll_mailbox_and_enqueue
from app.services.map_reduce import build_map_reduce_summary
from app.services.nas import run_nas_scan
from app.services.planner import plan_from_request
from app.services.search import search_documents
from app.services.source_tags import infer_source_type
from app.services.sync_run import (
    create_sync_run,
    execute_sync_run,
    get_sync_last,
    get_sync_source_summary,
    get_sync_summary,
    refresh_sync_run_status,
    start_sync_run,
)

settings = get_settings()
logger = get_logger(__name__)
router = APIRouter(prefix=settings.api_prefix)
DATA_DIR = (Path(__file__).resolve().parents[3] / "data").resolve()


_INLINE_MIME_BY_EXT: dict[str, str] = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "heic": "image/heic",
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
}


def _load_input_paths(raw_input_paths: str | None) -> list[str]:
    try:
        values = json.loads(raw_input_paths or "[]")
    except Exception:
        values = []
    if not isinstance(values, list):
        return []
    return [str(item or "").strip() for item in values if str(item or "").strip()]


def _job_retry_count(error_code: str | None) -> int:
    retry_count, _ = parse_retry_meta(error_code)
    return retry_count


def _parse_tag_query(raw: str | None) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    out: list[str] = []
    for part in text.split(","):
        value = str(part or "").strip()
        if not value:
            continue
        out.append(value)
    return out


def _normalize_disposition(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if value == "attachment":
        return "attachment"
    return "inline"


def _mime_for_file_ext(ext: str) -> str:
    normalized = str(ext or "").strip().lower().lstrip(".")
    if normalized in _INLINE_MIME_BY_EXT:
        return _INLINE_MIME_BY_EXT[normalized]
    guessed, _ = mimetypes.guess_type(f"file.{normalized}")
    return str(guessed or "").strip() or "application/octet-stream"


def _source_state_for_document(doc: Document) -> tuple[bool, str]:
    if str(doc.status or "") != DocumentStatus.COMPLETED.value:
        return (False, "document_not_ready")
    source_path = str(doc.source_path or "").strip()
    if (not source_path) or (not os.path.isfile(source_path)):
        return (False, "source_file_missing")
    return (True, "ok")


def _inline_supported_for_document(doc: Document) -> bool:
    file_ext = str(doc.file_ext or "").strip().lower().lstrip(".")
    media_type = _mime_for_file_ext(file_ext)
    return media_type != "application/octet-stream"


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(service=settings.app_name, version=settings.version, status="ok")


@router.get("/system/prompts", response_model=SystemPromptsResponse)
def get_system_prompts() -> SystemPromptsResponse:
    snap = prompt_snapshot()
    return SystemPromptsResponse(version=str(snap["version"]), hash=str(snap["hash"]), items=dict(snap["items"]))


@router.get("/governance/category-debt", response_model=GovernanceCategoryDebtResponse)
def governance_category_debt(
    top: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> GovernanceCategoryDebtResponse:
    snapshot = build_category_debt_snapshot(db, top_limit=int(top))
    return GovernanceCategoryDebtResponse(**snapshot)


@router.get(
    "/governance/category-debt/trend",
    response_model=GovernanceCategoryDebtTrendResponse,
)
def governance_category_debt_trend(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> GovernanceCategoryDebtTrendResponse:
    snapshots = load_snapshots_from_dir(data_dir=DATA_DIR, days=int(days))
    if not snapshots:
        snapshots = [build_category_debt_snapshot(db, top_limit=10)]
    trend = compute_debt_trend(snapshots)
    points = [GovernanceCategoryDebtTrendPoint(**item) for item in trend.get("points", [])]
    return GovernanceCategoryDebtTrendResponse(
        days=int(days),
        snapshot_count=len(points),
        week_over_week_change=int(trend.get("week_over_week_change") or 0),
        points=points,
    )


@router.post("/sync/runs", response_model=SyncRunStartResponse)
def start_sync(
    payload: SyncRunStartRequest | None = None,
    db: Session = Depends(get_db),
) -> SyncRunStartResponse:
    req = payload or SyncRunStartRequest()
    dispatch_status = "queued"
    dispatch_error = ""
    if settings.sync_run_async_enabled and (not settings.celery_task_always_eager):
        run = create_sync_run(db)
        try:
            celery_app.send_task(
                "fkv.sync.execute_run",
                args=[run.id],
                kwargs={
                    "nas_paths": list(req.nas_paths or []),
                    "recursive": bool(req.recursive),
                    "mail_max_results": req.mail_max_results,
                },
            )
        except Exception as exc:
            dispatch_status = "failed_to_dispatch"
            dispatch_error = str(type(exc).__name__)
            run = execute_sync_run(
                db,
                run.id,
                nas_paths=req.nas_paths,
                recursive=bool(req.recursive),
                mail_max_results=req.mail_max_results,
            )
            dispatch_status = "running" if str(run.status or "") == "running" else "queued"
    else:
        run = start_sync_run(
            db,
            nas_paths=req.nas_paths,
            recursive=bool(req.recursive),
            mail_max_results=req.mail_max_results,
        )
        dispatch_status = "running" if str(run.status or "") == "running" else "queued"
    nas_summary, mail_summary = get_sync_source_summary(run)
    last_run = (
        db.execute(select(SyncRun).where(SyncRun.id != run.id).order_by(SyncRun.started_at.desc()).limit(1))
        .scalars()
        .first()
    )
    logger.info(
        "sync_run_started",
        extra=sanitize_log_context(
            {
                "run_id": run.id,
                "status": run.status,
                "dispatch_status": dispatch_status,
                "item_count": int(
                    (nas_summary.get("changed_files") or 0) + (mail_summary.get("downloaded_attachments") or 0)
                ),
            }
        ),
    )
    return SyncRunStartResponse(
        run_id=run.id,
        status=run.status,
        started_at=run.started_at,
        last_sync_at=(last_run.finished_at or last_run.started_at) if last_run else None,
        dispatch_status=dispatch_status,
        dispatch_error=dispatch_error,
        nas=SyncSourceSummary(
            candidate_files=int(nas_summary.get("candidate_files") or 0),
            changed_files=int(nas_summary.get("changed_files") or 0),
            queued=bool(nas_summary.get("queued")),
            job_id=str(nas_summary.get("job_id") or ""),
        ),
        mail=SyncSourceSummary(
            polled_messages=int(mail_summary.get("polled_messages") or 0),
            processed_messages=int(mail_summary.get("processed_messages") or 0),
            downloaded_attachments=int(mail_summary.get("downloaded_attachments") or 0),
            queued=bool(mail_summary.get("queued")),
            job_id=str(mail_summary.get("job_id") or ""),
        ),
    )


@router.get("/sync/runs/{run_id}", response_model=SyncRunDetailResponse)
def get_sync_run_detail(run_id: str, db: Session = Depends(get_db)) -> SyncRunDetailResponse:
    run = refresh_sync_run_status(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="sync_run_not_found")

    summary = get_sync_summary(db, run)
    items = (
        db.execute(select(SyncRunItem).where(SyncRunItem.run_id == run.id).order_by(SyncRunItem.updated_at.desc()))
        .scalars()
        .all()
    )
    return SyncRunDetailResponse(
        run_id=run.id,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        summary=SyncRunSummary(**summary),
        items=[
            SyncRunItemResponse(
                item_id=item.id,
                source_type=item.source_type,
                file_name=item.file_name,
                file_size=int(item.file_size or 0),
                stage=item.stage,
                doc_id=item.doc_id,
                updated_at=item.updated_at,
                detail=item.detail,
            )
            for item in items
        ],
    )


@router.get("/sync/last", response_model=SyncLastResponse)
def get_last_sync(db: Session = Depends(get_db)) -> SyncLastResponse:
    run = get_sync_last(db)
    if run is None:
        return SyncLastResponse(last_sync_at=None, last_run_status="", last_run_id=None)
    return SyncLastResponse(
        last_sync_at=run.finished_at or run.started_at,
        last_run_status=run.status,
        last_run_id=run.id,
    )


@router.post("/ingestion/jobs", response_model=IngestionJobResponse)
def create_ingestion_job(payload: IngestionJobCreateRequest, db: Session = Depends(get_db)) -> IngestionJobResponse:
    input_paths = crud.filter_ignored_paths(db, payload.file_paths)
    if not input_paths:
        raise HTTPException(status_code=409, detail="all_paths_ignored")

    job = crud.create_ingestion_job(db, input_paths)
    mode = enqueue_ingestion_job(job.id)
    db.refresh(job)
    return IngestionJobResponse(
        job_id=job.id,
        status=job.status,
        input_paths=input_paths,
        success_count=job.success_count,
        failed_count=job.failed_count,
        duplicate_count=job.duplicate_count,
        error_code=job.error_code,
        retry_count=_job_retry_count(job.error_code),
        max_retries=settings.ingestion_retry_max_retries,
        queue_mode=mode,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.get("/ingestion/jobs/{job_id}", response_model=IngestionJobResponse)
def get_ingestion_job(job_id: str, db: Session = Depends(get_db)) -> IngestionJobResponse:
    job = crud.get_ingestion_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")

    input_paths = _load_input_paths(job.input_paths)

    return IngestionJobResponse(
        job_id=job.id,
        status=job.status,
        input_paths=input_paths,
        success_count=job.success_count,
        failed_count=job.failed_count,
        duplicate_count=job.duplicate_count,
        error_code=job.error_code,
        retry_count=_job_retry_count(job.error_code),
        max_retries=settings.ingestion_retry_max_retries,
        queue_mode="celery" if not settings.celery_task_always_eager else "sync",
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.delete("/ingestion/jobs/{job_id}", response_model=IngestionJobDeleteResponse)
def delete_ingestion_job(job_id: str, db: Session = Depends(get_db)) -> IngestionJobDeleteResponse:
    job = crud.get_ingestion_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")

    if job.status in {
        IngestionJobStatus.RUNNING.value,
        IngestionJobStatus.RETRYING.value,
    }:
        raise HTTPException(status_code=409, detail="job_is_active")

    input_paths = _load_input_paths(job.input_paths)
    ignored_count = crud.upsert_ignored_paths(db, input_paths, reason="queue_deleted")
    crud.delete_ingestion_job(db, job)
    logger.info(
        "ingestion_job_deleted",
        extra=sanitize_log_context({"job_id": job_id, "ignored_paths": ignored_count, "status": "deleted"}),
    )
    return IngestionJobDeleteResponse(
        job_id=job_id,
        deleted=True,
        ignored_paths=int(ignored_count),
        detail="deleted_and_ignored",
    )


@router.post("/ingestion/jobs/{job_id}/retry", response_model=IngestionJobResponse)
def retry_ingestion_job(job_id: str, db: Session = Depends(get_db)) -> IngestionJobResponse:
    source = crud.get_ingestion_job(db, job_id)
    if source is None:
        raise HTTPException(status_code=404, detail="job_not_found")

    if source.status in {
        IngestionJobStatus.RUNNING.value,
        IngestionJobStatus.RETRYING.value,
    }:
        raise HTTPException(status_code=409, detail="job_is_active")

    retryable = (int(source.failed_count or 0) > 0) or (source.status == IngestionJobStatus.FAILED.value)
    if not retryable:
        raise HTTPException(status_code=400, detail="job_not_retryable")

    input_paths = _load_input_paths(source.input_paths)
    input_paths = crud.filter_ignored_paths(db, input_paths)
    if not input_paths:
        raise HTTPException(status_code=409, detail="all_paths_ignored")

    job = crud.create_ingestion_job(db, input_paths)
    mode = enqueue_ingestion_job(job.id)
    db.refresh(job)
    logger.info(
        "ingestion_job_retry_requested",
        extra=sanitize_log_context({"source_job_id": source.id, "new_job_id": job.id, "status": mode}),
    )
    return IngestionJobResponse(
        job_id=job.id,
        status=job.status,
        input_paths=input_paths,
        success_count=job.success_count,
        failed_count=job.failed_count,
        duplicate_count=job.duplicate_count,
        error_code=job.error_code,
        retry_count=_job_retry_count(job.error_code),
        max_retries=settings.ingestion_retry_max_retries,
        queue_mode=mode,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.post("/ingestion/nas/scan", response_model=NasScanResponse)
def scan_nas(payload: NasScanRequest, db: Session = Depends(get_db)) -> NasScanResponse:
    result = run_nas_scan(
        db,
        paths=payload.paths,
        recursive=payload.recursive,
        max_files=payload.max_files,
    )
    return NasScanResponse(
        paths=result.get("paths") or [],
        candidate_files=int(result.get("candidate_files") or 0),
        changed_files=int(result.get("changed_files") or 0),
        missing_paths=int(result.get("missing_paths") or 0),
        queued=bool(result.get("queued")),
        queue_mode=str(result.get("queue_mode") or "none"),
        job_id=str(result.get("job_id") or ""),
    )


@router.post("/search", response_model=SearchResponse)
def search(payload: SearchRequest, db: Session = Depends(get_db)) -> SearchResponse:
    return search_documents(db, payload)


@router.get("/documents", response_model=DocumentListResponse)
def list_documents(
    status: str | None = Query(default=None),
    category_path: str | None = Query(default=None),
    q: str | None = Query(
        default=None,
        description="Metadata search over title/summary/file/category/tags.",
    ),
    tags_all: str | None = Query(default=None, description="Comma-separated tag keys; logical AND."),
    tags_any: str | None = Query(default=None, description="Comma-separated tag keys; logical OR."),
    include_missing: bool = Query(default=False),
    source_state: str | None = Query(default=None, pattern="^(available|missing|all)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> DocumentListResponse:
    tags_all_list = _parse_tag_query(tags_all)
    tags_any_list = _parse_tag_query(tags_any)
    items, total = crud.list_documents(
        db,
        status=status,
        category_path=category_path,
        tags_all=tags_all_list,
        tags_any=tags_any_list,
        include_missing=include_missing,
        source_state=source_state,
        q=q,
        limit=limit,
        offset=offset,
    )
    tag_map = crud.get_document_tags_map(db, [item.id for item in items])
    response_items: list[DocumentListItem] = []
    for row in items:
        source_available = crud.document_source_available_cached(row)
        response_items.append(
            DocumentListItem(
                doc_id=row.id,
                file_name=row.file_name,
                status=row.status,
                doc_lang=row.doc_lang,
                title_en=row.title_en,
                title_zh=row.title_zh,
                summary_en=row.summary_en or "",
                summary_zh=row.summary_zh or "",
                category_path=row.category_path,
                category_label_en=row.category_label_en or "",
                category_label_zh=row.category_label_zh or "",
                source_available=source_available,
                source_missing_reason="" if source_available else "source_file_missing",
                tags=tag_map.get(row.id, []),
                updated_at=row.updated_at,
            )
        )

    return DocumentListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=response_items,
    )


@router.get("/documents/{doc_id}", response_model=DocumentResponse)
def get_document(
    doc_id: str,
    include_chunks: bool = Query(default=False),
    chunk_limit: int = Query(default=0, ge=0, le=500),
    include_source_path: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> DocumentResponse:
    doc = crud.get_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document_not_found")

    include_chunks_effective = bool(include_chunks)
    chunks = []
    if include_chunks_effective:
        stmt = select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.chunk_index.asc())
        if chunk_limit > 0:
            stmt = stmt.limit(int(chunk_limit))
        chunks = db.execute(stmt).scalars().all()
    doc_tags = crud.get_document_tag_keys(db, doc.id)
    source_available = crud.document_source_available_cached(doc)

    return DocumentResponse(
        doc_id=doc.id,
        source_path=(doc.source_path if include_source_path else ""),
        source_path_included=bool(include_source_path),
        file_name=doc.file_name,
        file_ext=doc.file_ext,
        file_size=doc.file_size,
        sha256=doc.sha256,
        status=doc.status,
        duplicate_of=doc.duplicate_of,
        error_code=doc.error_code,
        doc_lang=doc.doc_lang,
        title_en=doc.title_en,
        title_zh=doc.title_zh,
        summary_en=doc.summary_en,
        summary_zh=doc.summary_zh,
        category_label_en=doc.category_label_en,
        category_label_zh=doc.category_label_zh,
        category_path=doc.category_path,
        summary_quality_state=str(doc.summary_quality_state or "unknown"),
        summary_last_error=str(doc.summary_last_error or ""),
        summary_model=str(doc.summary_model or ""),
        summary_version=str(doc.summary_version or "prompt-v2"),
        category_version=str(doc.category_version or "taxonomy-v1"),
        name_version=str(doc.name_version or "name-v2"),
        source_available=source_available,
        source_missing_reason="" if source_available else "source_file_missing",
        tags=doc_tags,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        chunks_included=include_chunks_effective,
        chunks=[
            DocumentChunk(
                chunk_id=item.id,
                chunk_index=item.chunk_index,
                token_count=item.token_count,
                content=item.content,
            )
            for item in chunks
        ],
        ocr_pages_total=doc.ocr_pages_total,
        ocr_pages_processed=doc.ocr_pages_processed,
        longdoc_mode=doc.longdoc_mode,
        longdoc_pages_total=doc.longdoc_pages_total,
        longdoc_pages_used=doc.longdoc_pages_used,
    )


@router.get(
    "/documents/{doc_id}/content/availability",
    response_model=DocumentContentAvailabilityResponse,
)
def get_document_content_availability(
    doc_id: str, db: Session = Depends(get_db)
) -> DocumentContentAvailabilityResponse:
    doc = crud.get_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document_not_found")

    source_available, detail = _source_state_for_document(doc)
    if detail != "document_not_ready":
        crud.set_document_source_available_cached(db, doc, available=source_available)
        db.commit()
    inline_supported = bool(source_available and _inline_supported_for_document(doc))
    if source_available and (not inline_supported):
        detail = "unsupported_media_type"
    return DocumentContentAvailabilityResponse(
        doc_id=str(doc.id),
        source_available=source_available,
        inline_supported=inline_supported,
        detail=detail,
    )


@router.get("/documents/{doc_id}/content")
def get_document_content(
    doc_id: str,
    disposition: str = Query(default="inline", pattern="^(inline|attachment)$"),
    db: Session = Depends(get_db),
):
    doc = crud.get_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    source_available, detail = _source_state_for_document(doc)
    if detail != "document_not_ready":
        crud.set_document_source_available_cached(db, doc, available=source_available)
        db.commit()
    if not source_available:
        if detail == "document_not_ready":
            raise HTTPException(status_code=409, detail=detail)
        raise HTTPException(status_code=404, detail=detail)

    safe_disposition = _normalize_disposition(disposition)
    file_ext = str(doc.file_ext or "").strip().lower().lstrip(".")
    media_type = _mime_for_file_ext(file_ext)
    if safe_disposition == "inline" and media_type == "application/octet-stream":
        raise HTTPException(status_code=415, detail="unsupported_media_type")

    response = FileResponse(
        path=str(doc.source_path),
        filename=str(doc.file_name or f"{doc.id}.{file_ext}" if file_ext else doc.id),
        media_type=media_type,
        content_disposition_type=safe_disposition,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, max-age=60"
    return response


@router.get("/queue", response_model=QueueResponse)
def get_queue(db: Session = Depends(get_db)) -> QueueResponse:
    jobs = db.execute(select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(20)).scalars().all()
    docs = db.execute(select(Document).order_by(Document.updated_at.desc()).limit(50)).scalars().all()

    totals = crud.get_queue_totals(db)
    return QueueResponse(
        jobs=[
            QueueJobItem(
                job_id=job.id,
                status=job.status,
                success_count=job.success_count,
                failed_count=job.failed_count,
                duplicate_count=job.duplicate_count,
                error_code=job.error_code,
                created_at=job.created_at,
            )
            for job in jobs
        ],
        documents=[
            QueueDocumentItem(
                doc_id=doc.id,
                file_name=doc.file_name,
                status=doc.status,
                updated_at=doc.updated_at,
            )
            for doc in docs
        ],
        totals=totals,
    )


@router.get("/categories", response_model=CategoriesResponse)
def get_categories(
    include_missing: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> CategoriesResponse:
    rows = crud.list_categories(db, include_missing=include_missing)
    return CategoriesResponse(
        total_categories=len(rows),
        items=[
            CategoryItem(
                category_path=str(row["category_path"]),
                label_en=str(row["label_en"]),
                label_zh=str(row["label_zh"]),
                doc_count=int(row["doc_count"]),
            )
            for row in rows
        ],
    )


@router.post("/documents/{doc_id}/reprocess", response_model=ReprocessResponse)
def reprocess_document(doc_id: str, db: Session = Depends(get_db)) -> ReprocessResponse:
    doc = crud.get_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    if not doc.source_path:
        raise HTTPException(status_code=400, detail="document_has_no_source_path")

    job = crud.create_ingestion_job(db, [doc.source_path])
    mode = enqueue_ingestion_job(job.id, force_reprocess=True, reprocess_doc_id=doc.id)
    logger.info(
        "document_reprocess_requested",
        extra=sanitize_log_context({"doc_id": doc_id, "step": "reprocess", "status": mode}),
    )
    return ReprocessResponse(doc_id=doc_id, job_id=job.id, status="queued")


@router.patch("/documents/{doc_id}/friendly-name", response_model=FriendlyNameResponse)
def update_document_friendly_name(
    doc_id: str,
    payload: FriendlyNameUpdateRequest,
    db: Session = Depends(get_db),
) -> FriendlyNameResponse:
    doc = crud.get_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document_not_found")

    has_zh = payload.friendly_name_zh is not None
    has_en = payload.friendly_name_en is not None
    if (not has_zh) and (not has_en):
        raise HTTPException(status_code=400, detail="friendly_name_missing")

    if has_zh:
        value = str(payload.friendly_name_zh or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail="friendly_name_zh_empty")
        doc.title_zh = value[:512]
    if has_en:
        value = str(payload.friendly_name_en or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail="friendly_name_en_empty")
        doc.title_en = value[:512]
    doc.name_version = "name-v2"
    db.commit()
    db.refresh(doc)
    return FriendlyNameResponse(
        doc_id=doc.id,
        friendly_name_zh=doc.title_zh,
        friendly_name_en=doc.title_en,
        updated_at=doc.updated_at,
    )


@router.get("/documents/{doc_id}/tags", response_model=DocumentTagsResponse)
def get_document_tags(doc_id: str, db: Session = Depends(get_db)) -> DocumentTagsResponse:
    doc = crud.get_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    rows = crud.get_document_tag_rows(db, doc_id)
    return DocumentTagsResponse(
        doc_id=doc_id,
        tags=[DocumentTagItem(**item) for item in crud.serialize_document_tags(rows)],
    )


@router.patch("/documents/{doc_id}/tags", response_model=DocumentTagsResponse)
def patch_document_tags(
    doc_id: str, payload: DocumentTagsPatchRequest, db: Session = Depends(get_db)
) -> DocumentTagsResponse:
    doc = crud.get_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    if (not payload.add) and (not payload.remove):
        raise HTTPException(status_code=400, detail="tags_patch_empty")

    rows, invalid = crud.patch_document_tags(db, document_id=doc_id, add=payload.add, remove=payload.remove)
    if invalid:
        detail = "invalid_tags:" + ",".join(invalid[:10])
        if "too_many_tags" in invalid:
            detail = "too_many_tags"
        if "too_many_topic_tags" in invalid:
            detail = "too_many_topic_tags"
        raise HTTPException(status_code=400, detail=detail)

    db.commit()
    rows = crud.get_document_tag_rows(db, doc_id)
    return DocumentTagsResponse(
        doc_id=doc_id,
        tags=[DocumentTagItem(**item) for item in crud.serialize_document_tags(rows)],
    )


@router.get("/tags", response_model=TagCatalogResponse)
def list_tags(
    family: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> TagCatalogResponse:
    rows = crud.list_tag_catalog(db, family=family, limit=limit)
    return TagCatalogResponse(
        total=len(rows),
        items=[TagCatalogItem(**item) for item in rows],
    )


@router.get("/tasks", response_model=TaskListResponse)
def list_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> TaskListResponse:
    rows, total = crud.list_tasks(db, limit=limit, offset=offset)
    return TaskListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[
            TaskListItem(
                task_id=item.id,
                title=item.title,
                task_type=item.task_type,
                status=item.status,
                updated_time=item.updated_time,
            )
            for item in rows
        ],
    )


@router.post("/tasks", response_model=TaskResponse)
def create_task(payload: TaskCreateRequest, db: Session = Depends(get_db)) -> TaskResponse:
    task = crud.create_task(
        db,
        {
            "title": payload.title,
            "task_type": payload.task_type,
            "doc_set": payload.doc_set,
            "filters": payload.filters,
        },
    )
    return TaskResponse(
        task_id=task.id,
        title=task.title,
        task_type=task.task_type,
        doc_set=payload.doc_set,
        filters=payload.filters,
        summary=BilingualText(en=task.summary_en, zh=task.summary_zh),
        status=task.status,
        created_time=task.created_time,
        updated_time=task.updated_time,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, db: Session = Depends(get_db)) -> TaskResponse:
    task = crud.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task_not_found")

    try:
        doc_set = json.loads(task.doc_set or "[]")
    except Exception:
        doc_set = []
    try:
        filters = json.loads(task.filters or "{}")
    except Exception:
        filters = {}

    return TaskResponse(
        task_id=task.id,
        title=task.title,
        task_type=task.task_type,
        doc_set=doc_set,
        filters=filters,
        summary=BilingualText(en=task.summary_en, zh=task.summary_zh),
        status=task.status,
        created_time=task.created_time,
        updated_time=task.updated_time,
    )


@router.post("/agent/plan", response_model=PlannerDecision)
def plan(payload: PlannerRequest, db: Session = Depends(get_db)) -> PlannerDecision:
    return plan_from_request(payload, db=db)


@router.post("/agent/execute", response_model=AgentExecuteResponse)
def execute(payload: AgentExecuteRequest, db: Session = Depends(get_db)) -> AgentExecuteResponse:
    try:
        return execute_agent(db, payload)
    except requests.exceptions.Timeout as exc:
        logger.warning(
            "agent_execute_http_error",
            extra=sanitize_log_context(
                {
                    "error_code": "agent_upstream_timeout",
                    "stage": str(getattr(exc, "fkv_stage", "") or "unknown"),
                    "trace_id": str(getattr(exc, "fkv_trace_id", "") or ""),
                }
            ),
        )
        raise HTTPException(status_code=504, detail="agent_upstream_timeout") from exc
    except Exception as exc:
        logger.warning(
            "agent_execute_http_error",
            extra=sanitize_log_context(
                {
                    "error_code": "agent_execute_failed",
                    "stage": str(getattr(exc, "fkv_stage", "") or "unknown"),
                    "trace_id": str(getattr(exc, "fkv_trace_id", "") or ""),
                    "exc_type": type(exc).__name__,
                }
            ),
        )
        raise HTTPException(status_code=503, detail="agent_execute_failed") from exc


_AGENT_STAGE_LABELS: dict[str, dict[str, str]] = {
    "node_planner": {"zh": "理解问题", "en": "Understanding query"},
    "node_route": {"zh": "规划路径", "en": "Planning route"},
    "node_structured_fastpath": {"zh": "快速检索", "en": "Fast retrieval"},
    "node_query_variant": {"zh": "扩展查询", "en": "Expanding query"},
    "node_retrieve": {"zh": "搜索文档", "en": "Searching documents"},
    "node_rerank": {"zh": "分析相关性", "en": "Ranking results"},
    "node_expand": {"zh": "扩展上下文", "en": "Expanding context"},
    "node_extract_slots": {"zh": "提取字段", "en": "Extracting fields"},
    "node_derive": {"zh": "推导事实", "en": "Deriving facts"},
    "node_judge": {"zh": "评估充分性", "en": "Assessing sufficiency"},
    "node_recovery_plan": {"zh": "补充检索", "en": "Recovery planning"},
    "node_recovery_apply": {"zh": "执行补充", "en": "Applying recovery"},
    "node_answer_build": {"zh": "生成回答", "en": "Generating answer"},
    "node_finalize": {"zh": "完成", "en": "Done"},
}


@router.post("/agent/execute/stream")
def agent_execute_stream(
    payload: AgentExecuteRequest,
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
) -> StreamingResponse:
    def _event_generator():
        try:
            for node_name, resp in stream_agent_graph(db, payload):
                label = _AGENT_STAGE_LABELS.get(node_name, {"zh": node_name, "en": node_name})
                if resp is not None:
                    event = {
                        "stage": node_name,
                        "label": label,
                        "done": True,
                        "result": resp.model_dump(),
                    }
                else:
                    event = {"stage": node_name, "label": label, "done": True}
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception:
            error_event = {"error": True, "detail": "internal_error"}
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/summaries/map-reduce", response_model=MapReduceSummaryResponse)
def map_reduce_summary(payload: MapReduceSummaryRequest, db: Session = Depends(get_db)) -> MapReduceSummaryResponse:
    try:
        out = build_map_reduce_summary(
            db,
            doc_id=payload.doc_id,
            ui_lang=payload.ui_lang,
            chunk_group_size=payload.chunk_group_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    doc = crud.get_document(db, payload.doc_id)
    if doc is None:
        out.applied = False
        out.apply_reason = "document_not_found"
        out.category_recomputed = False
        out.tags_recomputed = False
        out.qdrant_synced = False
        out.cascade_applied = False
        out.cascade_reason = "document_not_found"
        return out

    doc.summary_quality_state = str(out.quality_state or "needs_regen")[:24]
    doc.summary_model = str(get_model_setting("summary_model", db) or "")[:64]
    doc.summary_version = "prompt-v2"
    # Persist long-document sampling metadata so the UI can surface it
    doc.longdoc_mode = str(out.longdoc_mode or "normal")[:16]
    doc.longdoc_pages_total = int(out.pages_total or 0)
    doc.longdoc_pages_used = int(out.pages_used or 0)
    applied, apply_reason = apply_summary_to_doc(doc, out)
    category_recomputed = False
    tags_recomputed = False
    qdrant_synced = False
    cascade_applied = False
    cascade_reason = "summary_applied" if applied else str(apply_reason or "quality_not_ok")
    excerpt = load_chunk_excerpt(db, doc.id, limit=20)
    source_type = infer_source_type(str(doc.source_path or ""))
    doc_tags: list[str] = []

    if out.quality_state == "ok":
        category_recomputed = recompute_category(db, doc, excerpt)

        normalized_summary_en, normalized_summary_zh = normalize_vehicle_insurance_summary(
            category_path=doc.category_path,
            file_name=doc.file_name,
            summary_en=doc.summary_en,
            summary_zh=doc.summary_zh,
            content_excerpt=excerpt,
        )
        if normalized_summary_en != str(doc.summary_en or "") or normalized_summary_zh != str(doc.summary_zh or ""):
            doc.summary_en = normalized_summary_en[:2000]
            doc.summary_zh = normalized_summary_zh[:2000]
            try:
                out.short_summary.en = doc.summary_en
                out.short_summary.zh = doc.summary_zh
            except Exception:
                pass

        recompute_name_and_facts(db, doc, excerpt)

    if out.quality_state == "ok":
        tags_recomputed, doc_tags = recompute_tags(db, doc, excerpt, source_type)

    # Commit all DB changes before Qdrant upsert: Qdrant calls Ollama for embeddings
    # (external network, up to 20s) and must not hold the SQLite write lock.
    db.commit()
    db.refresh(doc)

    if out.quality_state == "ok":
        qdrant_synced = sync_to_qdrant(db, doc, source_type, doc_tags)
    if out.quality_state == "ok":
        if not applied:
            cascade_reason = str(apply_reason or "quality_not_ok")
        elif not category_recomputed:
            cascade_reason = "category_not_recomputed"
        elif not tags_recomputed:
            cascade_reason = "tags_not_recomputed"
        elif not qdrant_synced:
            cascade_reason = "qdrant_error"
        else:
            cascade_applied = True
            cascade_reason = "ok"
    out.applied = bool(applied)
    out.apply_reason = str(apply_reason or "unknown")
    out.category_recomputed = bool(category_recomputed)
    out.tags_recomputed = bool(tags_recomputed)
    out.qdrant_synced = bool(qdrant_synced)
    out.cascade_applied = bool(cascade_applied)
    out.cascade_reason = str(cascade_reason or "")
    return out


class MapReduceAsyncResponse(BaseModel):
    task_id: str
    doc_id: str
    status: str = "queued"


class MapReduceStatusResponse(BaseModel):
    doc_id: str
    job_status: str
    page_summaries_available: bool
    section_summaries_available: bool
    pages_done: int
    pages_total: int


@router.post("/summaries/map-reduce/async", response_model=MapReduceAsyncResponse)
def map_reduce_summary_async(payload: MapReduceSummaryRequest, db: Session = Depends(get_db)) -> MapReduceAsyncResponse:
    """Dispatch a map-reduce summarisation job asynchronously via Celery.

    Returns immediately with a task_id. Poll GET /summaries/map-reduce/status/{doc_id}
    to track progress. The synchronous POST /summaries/map-reduce endpoint is
    unchanged for backward compatibility.
    """
    doc = crud.get_document(db, payload.doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {payload.doc_id}")
    task = celery_app.send_task(
        "fkv.map_reduce.process",
        kwargs={
            "doc_id": payload.doc_id,
            "ui_lang": str(payload.ui_lang or "zh"),
            "chunk_group_size": int(payload.chunk_group_size or 6),
        },
    )
    doc.mapreduce_job_status = "queued"
    db.commit()
    return MapReduceAsyncResponse(task_id=str(task.id), doc_id=payload.doc_id)


@router.get("/summaries/map-reduce/status/{doc_id}", response_model=MapReduceStatusResponse)
def map_reduce_status(doc_id: str, db: Session = Depends(get_db)) -> MapReduceStatusResponse:
    """Return the current map-reduce checkpoint status for a document.

    ``job_status`` progresses through:
      ``queued`` → ``pages_N/T`` → ``sections_N/T`` → ``completed``
    """
    doc = crud.get_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    job_status = str(doc.mapreduce_job_status or "")
    pages_done = 0
    pages_total = 0
    if job_status.startswith("pages_"):
        _parts = job_status[len("pages_") :].split("/")
        if len(_parts) == 2:
            try:
                pages_done = int(_parts[0])
                pages_total = int(_parts[1])
            except ValueError:
                pass
    page_summaries_available = str(doc.mapreduce_page_summaries_json or "[]").strip() not in ("", "[]")
    section_summaries_available = str(doc.mapreduce_section_summaries_json or "[]").strip() not in ("", "[]")
    return MapReduceStatusResponse(
        doc_id=doc_id,
        job_status=job_status,
        page_summaries_available=page_summaries_available,
        section_summaries_available=section_summaries_available,
        pages_done=pages_done,
        pages_total=pages_total,
    )


@router.post("/mail/poll", response_model=MailPollResponse)
def poll_mailbox(payload: MailPollRequest | None = None, db: Session = Depends(get_db)) -> MailPollResponse:
    out = poll_mailbox_and_enqueue(db, max_results=(payload.max_results if payload else None))
    return MailPollResponse(
        polled_messages=int(out.get("polled_messages") or 0),
        processed_messages=int(out.get("processed_messages") or 0),
        downloaded_attachments=int(out.get("downloaded_attachments") or 0),
        queued=bool(out.get("queued")),
        queue_mode=str(out.get("queue_mode") or "none"),
        job_id=str(out.get("job_id") or ""),
        detail=str(out.get("detail") or ""),
    )


@router.get("/mail/health", response_model=MailHealthResponse)
def mail_health() -> MailHealthResponse:
    if not bool(settings.mail_poll_enabled):
        return MailHealthResponse(enabled=False, status="disabled", detail="Mail polling is disabled")
    result = get_gmail_health()
    return MailHealthResponse(enabled=True, status=result["status"], detail=result["detail"])


@router.get("/mail/events", response_model=MailEventsResponse)
def list_mail_events(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> MailEventsResponse:
    safe_limit = max(1, min(500, int(limit)))
    safe_offset = max(0, int(offset))
    rows = (
        db.execute(
            select(MailIngestionEvent)
            .order_by(MailIngestionEvent.created_at.desc())
            .offset(safe_offset)
            .limit(safe_limit)
        )
        .scalars()
        .all()
    )
    total = int(db.scalar(select(func.count()).select_from(MailIngestionEvent)) or 0)
    return MailEventsResponse(
        total=total,
        items=[
            MailEventItem(
                id=item.id,
                message_id=item.message_id,
                subject=item.subject,
                from_addr=item.from_addr,
                attachment_name=item.attachment_name,
                attachment_path=item.attachment_path,
                status=item.status,
                detail=item.detail,
                created_at=item.created_at,
            )
            for item in rows
        ],
    )


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


class _SetupRequest(BaseModel):
    password: str


class _LoginRequest(BaseModel):
    password: str


class _ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.get("/auth/status")
def auth_status(db: Session = Depends(get_db)):
    """Returns whether initial setup is complete. No auth required."""
    return {"setup_complete": is_setup_complete(db)}


@router.post("/auth/setup")
def auth_setup(body: _SetupRequest, db: Session = Depends(get_db)):
    """Set the initial admin password. Only callable when setup is not yet complete."""
    if is_setup_complete(db):
        raise HTTPException(status_code=400, detail="Setup already complete.")
    if len(body.password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters.")
    set_admin_password(body.password, db)
    return {"ok": True}


@router.post("/auth/login")
def auth_login(body: _LoginRequest, response: Response, db: Session = Depends(get_db)):
    """Verify password and set JWT cookie."""
    if not is_setup_complete(db):
        raise HTTPException(status_code=400, detail="Setup not complete.")
    if not verify_admin_password(body.password, db):
        raise HTTPException(status_code=401, detail="Invalid password.")
    token = create_access_token()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,
        secure=settings.cookie_secure,
    )
    return {"ok": True}


@router.post("/auth/logout")
def auth_logout(response: Response):
    """Clear the JWT cookie."""
    response.delete_cookie(key=COOKIE_NAME, httponly=True, samesite="lax", secure=settings.cookie_secure)
    return {"ok": True}


@router.patch("/auth/password")
def auth_change_password(
    body: _ChangePasswordRequest,
    db: Session = Depends(get_db),
    fkv_token: str | None = Cookie(default=None),
):
    """Change the admin password (requires current session)."""
    from app.auth import decode_access_token

    if not fkv_token or not decode_access_token(fkv_token):
        raise HTTPException(status_code=401, detail="Not authenticated.")
    if not verify_admin_password(body.old_password, db):
        raise HTTPException(status_code=401, detail="Old password incorrect.")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=422, detail="New password must be at least 8 characters.")
    set_admin_password(body.new_password, db)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Settings endpoints
# ---------------------------------------------------------------------------

import os as _os  # noqa: E402

from app.runtime_config import (  # noqa: E402
    _RUNTIME_CONFIGURABLE,
    SETTING_META,
    get_model_setting,
    get_runtime_json,
    get_runtime_setting,
    invalidate_runtime_cache,
)

# Settings that require worker restart to take effect
RESTART_REQUIRED_KEYS = {
    "planner_model",
    "synthesizer_model",
    "embed_model",
    "summary_model",
    "category_model",
    "friendly_name_model",
    "vl_extract_model",
    "summary_timeout_page_sec",
    "summary_timeout_section_sec",
    "summary_timeout_final_sec",
    "agent_synth_timeout_sec",
    "ollama_base_url",
}


def _setting_source(key: str, db: Session) -> str:
    env_var, _ = _RUNTIME_CONFIGURABLE[key]
    row = db.get(AppSetting, key)
    if row is not None:
        return "db"
    if env_var and _os.environ.get(env_var):
        return "env"
    return "default"


@router.get("/settings")
def get_settings_endpoint(
    _: object = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all runtime-configurable settings with their current values."""
    items = []
    for key in _RUNTIME_CONFIGURABLE:
        if key in ("person_keywords", "pet_keywords", "location_keywords"):
            continue  # returned via /settings/keywords
        meta = SETTING_META.get(key, {})
        items.append(
            {
                "key": key,
                "value": get_runtime_setting(key, db),
                "source": _setting_source(key, db),
                "type": meta.get("type", "string"),
                "category": meta.get("category", "advanced"),
                "label_zh": meta.get("label_zh", key),
                "label_en": meta.get("label_en", key),
            }
        )
    return {"items": items}


@router.patch("/settings")
def patch_settings(
    body: dict,
    _: object = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update one or more settings in the DB."""
    for key, value in body.items():
        if key not in _RUNTIME_CONFIGURABLE:
            raise HTTPException(status_code=400, detail=f"Unknown setting: {key!r}")
        str_value = str(value) if not isinstance(value, str) else value
        row = db.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=str_value, updated_at=dt.datetime.now(dt.UTC))
            db.add(row)
        else:
            row.value = str_value
            row.updated_at = dt.datetime.now(dt.UTC)
    db.commit()
    invalidate_runtime_cache(*list(body.keys()))

    # Check if restart is required
    restart_required = bool(set(body.keys()) & RESTART_REQUIRED_KEYS)
    return {"ok": True, "restart_required": restart_required}


@router.get("/settings/keywords")
def get_keywords(
    _: object = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return user-defined tagging keyword lists."""
    return {
        "person_keywords": get_runtime_json("person_keywords", db).get("terms", {}),
        "pet_keywords": get_runtime_json("pet_keywords", db).get("terms", {}),
        "location_keywords": get_runtime_json("location_keywords", db).get("terms", {}),
    }


@router.patch("/settings/keywords")
def patch_keywords(
    body: dict,
    _: object = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Update keyword lists. body keys: person_keywords | pet_keywords | location_keywords.
    Each value is a list of strings (or dict {term: canonical}).
    """
    import json as _json

    valid_keys = {"person_keywords", "pet_keywords", "location_keywords"}
    for key, terms in body.items():
        if key not in valid_keys:
            raise HTTPException(status_code=400, detail=f"Unknown keyword list: {key!r}")
        if isinstance(terms, list):
            terms_dict = {t.lower(): t.lower() for t in terms}
        elif isinstance(terms, dict):
            terms_dict = {k.lower(): v.lower() for k, v in terms.items()}
        else:
            raise HTTPException(status_code=422, detail=f"{key} must be a list or dict")
        str_value = _json.dumps({"terms": terms_dict})
        row = db.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=str_value, updated_at=dt.datetime.now(dt.UTC))
            db.add(row)
        else:
            row.value = str_value
            row.updated_at = dt.datetime.now(dt.UTC)
    db.commit()
    invalidate_runtime_cache(*list(body.keys()))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Ollama model list proxy
# ---------------------------------------------------------------------------


@router.get("/ollama/models")
def get_ollama_models(db: Session = Depends(get_db)):
    """Proxy Ollama /api/tags to return available models."""
    ollama_url = get_runtime_setting("ollama_base_url", db)
    try:
        resp = requests.get(f"{ollama_url}/api/tags", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        models_list = [{"name": m.get("name", ""), "size": m.get("size", 0)} for m in data.get("models", [])]
        return {"models": models_list, "ok": True}
    except Exception as exc:
        return {"models": [], "ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Connectivity health check
# ---------------------------------------------------------------------------


@router.get("/health/connectivity")
def connectivity_health(db: Session = Depends(get_db)):
    """Check connectivity to Ollama, Qdrant, NAS, and Gmail."""
    import time

    ollama_url = get_runtime_setting("ollama_base_url", db)

    def _check_ollama() -> dict:
        try:
            t0 = time.monotonic()
            resp = requests.get(f"{ollama_url}/api/tags", timeout=5)
            resp.raise_for_status()
            model_count = len(resp.json().get("models", []))
            return {
                "ok": True,
                "model_count": model_count,
                "latency_ms": int((time.monotonic() - t0) * 1000),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "model_count": 0}

    def _check_qdrant() -> dict:
        try:
            qdrant_resp = requests.get(f"{settings.qdrant_url}/collections/{settings.qdrant_collection}", timeout=5)
            return {
                "ok": qdrant_resp.status_code == 200,
                "collection": settings.qdrant_collection,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=2) as executor:
        ollama_future = executor.submit(_check_ollama)
        qdrant_future = executor.submit(_check_qdrant)
        ollama_result = ollama_future.result()
        qdrant_result = qdrant_future.result()

    # Source directory (local/NAS)
    from app.services.path_scan import resolve_source_root

    source_type, source_root = resolve_source_root(db)
    nas_dir = str(source_root or "").strip()
    nas_result = {
        "ok": False,
        "source_type": source_type,
        "path": nas_dir,
        "readable": False,
        "writable": False,
        "error": None,
    }
    if not nas_dir:
        nas_result["error"] = "nas directory is empty"
    elif not os.path.isdir(nas_dir):
        nas_result["error"] = "directory not found"
    else:
        readable = bool(os.access(nas_dir, os.R_OK))
        writable = bool(os.access(nas_dir, os.W_OK))
        read_err = ""
        write_err = ""

        # 读权限实测：尝试列举目录项，避免仅依赖 os.access 的假阳性。
        if readable:
            try:
                with os.scandir(nas_dir) as entries:
                    next(entries, None)
            except Exception as exc:
                readable = False
                read_err = str(exc)

        # 写权限实测：创建并删除临时文件，覆盖创建/写入/清理完整链路。
        test_path = os.path.join(nas_dir, f".fkv_rw_test_{int(time.time() * 1000)}")
        if writable:
            try:
                with open(test_path, "w", encoding="utf-8") as fh:
                    fh.write("ok")
                os.remove(test_path)
            except Exception as exc:
                writable = False
                write_err = str(exc)
                try:
                    if os.path.exists(test_path):
                        os.remove(test_path)
                except Exception:
                    pass

        error = None
        if not readable:
            error = read_err or "directory not readable"
        elif not writable:
            error = write_err or "directory not writable"
        nas_result.update(
            {
                "readable": readable,
                "writable": writable,
                "ok": bool(readable and writable),
                "error": error,
            }
        )

    # Gmail credentials
    creds_path = settings.mail_credentials_path
    token_path = settings.mail_token_path
    gmail_result = {
        "ok": os.path.isfile(creds_path) and os.path.isfile(token_path),
        "credentials_present": os.path.isfile(creds_path),
        "token_present": os.path.isfile(token_path),
    }

    return {
        "ollama": ollama_result,
        "qdrant": qdrant_result,
        "nas": nas_result,
        "gmail": gmail_result,
    }


# ---------------------------------------------------------------------------
# Service restart
# ---------------------------------------------------------------------------


@router.post("/restart")
def restart_services(_: object = Depends(get_current_user)):
    """Restart backend worker to apply configuration changes."""
    manual_cmd = "docker compose restart fkv-worker"

    docker_bin = shutil.which("docker")
    if docker_bin:
        try:
            result = subprocess.run(
                [docker_bin, "compose", "restart", "fkv-worker"],
                check=False,
                capture_output=True,
                text=True,
                timeout=45,
            )
            if result.returncode == 0:
                return {
                    "ok": True,
                    "message": "Worker restart requested successfully via docker compose.",
                }
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            detail = stderr or stdout or "docker compose returned non-zero exit code"
            return {
                "ok": False,
                "manual": True,
                "error": (f"Automatic restart failed: {detail[:300]}. Please run manually: {manual_cmd}"),
                "message": f"Please run manually: {manual_cmd}",
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "manual": True,
                "error": (f"Automatic restart timed out. Please run manually: {manual_cmd}"),
                "message": f"Please run manually: {manual_cmd}",
            }
        except Exception as exc:
            return {
                "ok": False,
                "manual": True,
                "error": (f"Automatic restart failed: {str(exc)[:300]}. Please run manually: {manual_cmd}"),
                "message": f"Please run manually: {manual_cmd}",
            }

    return {
        "ok": False,
        "manual": True,
        "error": (f"Docker CLI is not available in this environment. Please run manually: {manual_cmd}"),
        "message": f"Please run manually: {manual_cmd}",
    }


_root_router = APIRouter()
_root_router.include_router(router)
_root_router.include_router(auth_router)
# Backward-compatible auth endpoints for clients calling /api/v1/auth/* directly.
_root_router.include_router(auth_router, prefix="/api")
router = _root_router


# ---------------------------------------------------------------------------
# Gmail Credentials API
# ---------------------------------------------------------------------------


@router.get("/gmail/credentials")
def list_gmail_credentials(
    _: object = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all Gmail credentials (masked)."""
    from app.models import GmailCredentials

    creds = db.execute(select(GmailCredentials).where(GmailCredentials.is_active.is_(True))).scalars().all()
    items = []
    for c in creds:
        client_id_masked = c.client_id[:8] + "..." + c.client_id[-4:] if len(c.client_id) > 12 else c.client_id
        items.append(
            {
                "id": c.id,
                "name": c.name,
                "client_id_masked": client_id_masked,
                "redirect_uri": c.redirect_uri,
                "is_active": c.is_active,
                "created_at": c.created_at.isoformat(),
                "updated_at": c.updated_at.isoformat(),
            }
        )
    return {"items": items, "total": len(items)}


@router.post("/gmail/credentials")
def create_gmail_credentials(
    body: dict,
    _: object = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new Gmail credential."""
    import uuid

    from app.models import GmailCredentials
    from app.utils.encryption import encrypt

    cred = GmailCredentials(
        id=str(uuid.uuid4()),
        name=body["name"],
        client_id=body["client_id"],
        client_secret_encrypted=encrypt(body["client_secret"]),
        redirect_uri=body.get("redirect_uri", "http://localhost"),
        token_encrypted=encrypt(body["token"]) if body.get("token") else None,
        refresh_token_encrypted=encrypt(body["refresh_token"]) if body.get("refresh_token") else None,
        token_uri=body.get("token_uri", "https://oauth2.googleapis.com/token"),
        auth_uri=body.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
        scopes=body.get("scopes", "https://www.googleapis.com/auth/gmail.readonly"),
    )
    db.add(cred)
    db.commit()
    return {"ok": True, "id": cred.id}


@router.put("/gmail/credentials/{cred_id}")
def update_gmail_credentials(
    cred_id: str,
    body: dict,
    _: object = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a Gmail credential."""
    from app.models import GmailCredentials
    from app.utils.encryption import encrypt

    cred = db.get(GmailCredentials, cred_id)
    if not cred:
        return {"ok": False, "error": "Not found"}, 404

    if "name" in body:
        cred.name = body["name"]
    if "client_id" in body:
        cred.client_id = body["client_id"]
    if "client_secret" in body:
        cred.client_secret_encrypted = encrypt(body["client_secret"])
    if "token" in body:
        cred.token_encrypted = encrypt(body["token"]) if body["token"] else None
    if "refresh_token" in body:
        cred.refresh_token_encrypted = encrypt(body["refresh_token"]) if body["refresh_token"] else None
    if "is_active" in body:
        cred.is_active = body["is_active"]

    db.commit()
    return {"ok": True}


@router.delete("/gmail/credentials/{cred_id}")
def delete_gmail_credentials(
    cred_id: str,
    _: object = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a Gmail credential."""
    from app.models import GmailCredentials

    cred = db.get(GmailCredentials, cred_id)
    if not cred:
        return {"ok": False, "error": "Not found"}, 404

    db.delete(cred)
    db.commit()
    return {"ok": True}
