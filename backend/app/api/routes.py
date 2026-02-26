import datetime as dt
import json
import mimetypes
import os
from pathlib import Path
import requests

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import crud
from app.api.deps import get_db
from app.config import get_settings
from app.celery_app import celery_app
from app.logging_utils import get_logger, sanitize_log_context
from app.models import Chunk, Document, DocumentStatus, IngestionJob, IngestionJobStatus, MailIngestionEvent, SyncRun, SyncRunItem, Task
from app.schemas import (
    AgentExecuteRequest,
    AgentExecuteResponse,
    CategoriesResponse,
    CategoryItem,
    DocumentChunk,
    FriendlyNameUpdateRequest,
    FriendlyNameResponse,
    GovernanceCategoryDebtResponse,
    GovernanceCategoryDebtTrendPoint,
    GovernanceCategoryDebtTrendResponse,
    DocumentListItem,
    DocumentListResponse,
    DocumentContentAvailabilityResponse,
    DocumentResponse,
    DocumentTagItem,
    DocumentTagsPatchRequest,
    DocumentTagsResponse,
    HealthResponse,
    IngestionJobCreateRequest,
    IngestionJobDeleteResponse,
    IngestionJobResponse,
    MailEventItem,
    MailEventsResponse,
    MailHealthResponse,
    MailPollRequest,
    MailPollResponse,
    NasScanRequest,
    NasScanResponse,
    PlannerDecision,
    PlannerRequest,
    MapReduceSummaryRequest,
    MapReduceSummaryResponse,
    SystemPromptsResponse,
    QueueDocumentItem,
    QueueJobItem,
    QueueResponse,
    ReprocessResponse,
    SyncLastResponse,
    SyncRunDetailResponse,
    SyncRunItemResponse,
    SyncRunStartRequest,
    SyncRunStartResponse,
    SyncRunSummary,
    SyncSourceSummary,
    SearchRequest,
    SearchResponse,
    TagCatalogItem,
    TagCatalogResponse,
    TaskCreateRequest,
    TaskListItem,
    TaskListResponse,
    TaskResponse,
    BilingualText,
)
from app.services.agent import execute_agent
from app.services.agent_graph import stream_agent_graph
from app.services.governance import apply_legacy_category_guard, build_category_debt_snapshot, compute_debt_trend, load_snapshots_from_dir
from app.services.sync_run import (
    create_sync_run,
    execute_sync_run,
    get_sync_last,
    get_sync_source_summary,
    get_sync_summary,
    refresh_sync_run_status,
    start_sync_run,
)
from app.services.ingestion import enqueue_ingestion_job, parse_retry_meta
from app.services.llm_summary import (
    classify_category_from_summary,
    normalize_vehicle_insurance_summary,
    prompt_snapshot,
    regenerate_friendly_name_from_summary,
)
from app.services.mail_ingest import get_gmail_health, poll_mailbox_and_enqueue
from app.services.map_reduce import build_map_reduce_summary
from app.services.nas import run_nas_scan
from app.services.planner import plan_from_request
from app.services.qdrant import qdrant_payload, upsert_records
from app.services.bill_facts import upsert_bill_fact_for_document
from app.services.search import search_documents
from app.services.source_tags import category_labels_for_path, infer_source_type
from app.services.tag_rules import infer_auto_tags


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


@router.get("/governance/category-debt/trend", response_model=GovernanceCategoryDebtTrendResponse)
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
    last_run = db.execute(select(SyncRun).where(SyncRun.id != run.id).order_by(SyncRun.started_at.desc()).limit(1)).scalars().first()
    logger.info(
        "sync_run_started",
        extra=sanitize_log_context(
            {
                "run_id": run.id,
                "status": run.status,
                "dispatch_status": dispatch_status,
                "item_count": int((nas_summary.get("changed_files") or 0) + (mail_summary.get("downloaded_attachments") or 0)),
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
    return SyncLastResponse(last_sync_at=run.finished_at or run.started_at, last_run_status=run.status, last_run_id=run.id)


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

    if job.status in {IngestionJobStatus.RUNNING.value, IngestionJobStatus.RETRYING.value}:
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

    if source.status in {IngestionJobStatus.RUNNING.value, IngestionJobStatus.RETRYING.value}:
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
    q: str | None = Query(default=None, description="Metadata search over title/summary/file/category/tags."),
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
    return DocumentListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[
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
                source_available=crud.document_source_available_cached(row),
                source_missing_reason="" if crud.document_source_available_cached(row) else "source_file_missing",
                tags=tag_map.get(row.id, []),
                updated_at=row.updated_at,
            )
            for row in items
        ],
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
            DocumentChunk(chunk_id=item.id, chunk_index=item.chunk_index, token_count=item.token_count, content=item.content)
            for item in chunks
        ],
        ocr_pages_total=doc.ocr_pages_total,
        ocr_pages_processed=doc.ocr_pages_processed,
        longdoc_mode=doc.longdoc_mode,
        longdoc_pages_total=doc.longdoc_pages_total,
        longdoc_pages_used=doc.longdoc_pages_used,
    )


@router.get("/documents/{doc_id}/content/availability", response_model=DocumentContentAvailabilityResponse)
def get_document_content_availability(doc_id: str, db: Session = Depends(get_db)) -> DocumentContentAvailabilityResponse:
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
            QueueDocumentItem(doc_id=doc.id, file_name=doc.file_name, status=doc.status, updated_at=doc.updated_at) for doc in docs
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
    logger.info("document_reprocess_requested", extra=sanitize_log_context({"doc_id": doc_id, "step": "reprocess", "status": mode}))
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
def patch_document_tags(doc_id: str, payload: DocumentTagsPatchRequest, db: Session = Depends(get_db)) -> DocumentTagsResponse:
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
def plan(payload: PlannerRequest) -> PlannerDecision:
    return plan_from_request(payload)


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
    "node_planner":            {"zh": "理解问题", "en": "Understanding query"},
    "node_route":              {"zh": "规划路径", "en": "Planning route"},
    "node_structured_fastpath":{"zh": "快速检索", "en": "Fast retrieval"},
    "node_query_variant":      {"zh": "扩展查询", "en": "Expanding query"},
    "node_retrieve":           {"zh": "搜索文档", "en": "Searching documents"},
    "node_rerank":             {"zh": "分析相关性", "en": "Ranking results"},
    "node_expand":             {"zh": "扩展上下文", "en": "Expanding context"},
    "node_extract_slots":      {"zh": "提取字段", "en": "Extracting fields"},
    "node_derive":             {"zh": "推导事实", "en": "Deriving facts"},
    "node_judge":              {"zh": "评估充分性", "en": "Assessing sufficiency"},
    "node_recovery_plan":      {"zh": "补充检索", "en": "Recovery planning"},
    "node_recovery_apply":     {"zh": "执行补充", "en": "Applying recovery"},
    "node_answer_build":       {"zh": "生成回答", "en": "Generating answer"},
    "node_finalize":           {"zh": "完成", "en": "Done"},
}


@router.post("/agent/execute/stream")
def agent_execute_stream(payload: AgentExecuteRequest, db: Session = Depends(get_db)) -> StreamingResponse:
    def _event_generator():
        try:
            for node_name, resp in stream_agent_graph(db, payload):
                label = _AGENT_STAGE_LABELS.get(node_name, {"zh": node_name, "en": node_name})
                if resp is not None:
                    event = {"stage": node_name, "label": label, "done": True, "result": resp.model_dump()}
                else:
                    event = {"stage": node_name, "label": label, "done": True}
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            error_event = {"error": True, "detail": str(exc)}
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/summaries/map-reduce", response_model=MapReduceSummaryResponse)
def map_reduce_summary(payload: MapReduceSummaryRequest, db: Session = Depends(get_db)) -> MapReduceSummaryResponse:
    try:
        out = build_map_reduce_summary(db, doc_id=payload.doc_id, ui_lang=payload.ui_lang, chunk_group_size=payload.chunk_group_size)
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

    summary_en = str((out.short_summary.en if out.short_summary else "") or "").strip()
    summary_zh = str((out.short_summary.zh if out.short_summary else "") or "").strip()
    doc.summary_quality_state = str(out.quality_state or "needs_regen")[:24]
    doc.summary_model = str(settings.summary_model or "")[:64]
    doc.summary_version = "prompt-v2"
    # Persist long-document sampling metadata so the UI can surface it
    doc.longdoc_mode = str(out.longdoc_mode or "normal")[:16]
    doc.longdoc_pages_total = int(out.pages_total or 0)
    doc.longdoc_pages_used = int(out.pages_used or 0)
    applied = False
    apply_reason = "quality_not_ok"
    category_recomputed = False
    tags_recomputed = False
    qdrant_synced = False
    cascade_applied = False
    cascade_reason = "summary_not_ok"

    if out.quality_state == "ok" and (summary_en or summary_zh):
        doc.summary_en = summary_en[:2000]
        doc.summary_zh = summary_zh[:2000]
        doc.summary_last_error = ""
        applied = True
        apply_reason = "ok"
        cascade_reason = "summary_applied"
    else:
        detail = ",".join(str(x or "").strip() for x in out.quality_flags if str(x or "").strip())
        doc.summary_last_error = (detail or str(out.quality_state or "needs_regen"))[:240]
        apply_reason = str(out.quality_state or "quality_not_ok")
        cascade_reason = str(out.quality_state or "quality_not_ok")

    chunks = db.execute(select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.chunk_index.asc()).limit(20)).scalars().all()
    excerpt = "\n".join(str(item.content or "") for item in chunks)[:3200]
    source_type = infer_source_type(str(doc.source_path or ""))

    if out.quality_state == "ok":
        classified = classify_category_from_summary(
            file_name=doc.file_name,
            source_type=source_type,
            summary_en=doc.summary_en,
            summary_zh=doc.summary_zh,
            content_excerpt=excerpt,
        )
        if classified is not None:
            cat_en, cat_zh, cat_path = classified
            guarded_path, blocked_legacy = apply_legacy_category_guard(cat_path)
            if blocked_legacy:
                logger.warning(
                    "legacy_category_blocked",
                    extra=sanitize_log_context(
                        {
                            "event": "legacy_category_blocked",
                            "doc_id": doc.id,
                            "original_path": cat_path,
                            "rewritten_path": guarded_path,
                            "pipeline_stage": "map_reduce_classify",
                        }
                    ),
                )
            doc.category_label_en = str(cat_en or "")[:128]
            doc.category_label_zh = str(cat_zh or "")[:128]
            doc.category_path = str(guarded_path or "")[:256]
            if blocked_legacy:
                safe_en, safe_zh = category_labels_for_path(doc.category_path)
                doc.category_label_en = str(safe_en or "")[:128]
                doc.category_label_zh = str(safe_zh or "")[:128]
            doc.category_version = "taxonomy-v1"
            category_recomputed = True
        elif (not str(doc.category_label_en or "").strip()) or (not str(doc.category_label_zh or "").strip()):
            default_en, default_zh = category_labels_for_path(doc.category_path)
            doc.category_label_en = str(default_en or "")[:128]
            doc.category_label_zh = str(default_zh or "")[:128]
        guarded_existing, blocked_existing = apply_legacy_category_guard(doc.category_path)
        if blocked_existing:
            logger.warning(
                "legacy_category_blocked",
                extra=sanitize_log_context(
                    {
                        "event": "legacy_category_blocked",
                        "doc_id": doc.id,
                        "original_path": doc.category_path,
                        "rewritten_path": guarded_existing,
                        "pipeline_stage": "map_reduce_finalize",
                    }
                ),
            )
            safe_en, safe_zh = category_labels_for_path(guarded_existing)
            doc.category_path = str(guarded_existing or "")[:256]
            doc.category_label_en = str(safe_en or "")[:128]
            doc.category_label_zh = str(safe_zh or "")[:128]

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

        renamed = regenerate_friendly_name_from_summary(
            file_name=doc.file_name,
            category_path=doc.category_path,
            summary_en=doc.summary_en,
            summary_zh=doc.summary_zh,
            fallback_en=doc.title_en,
            fallback_zh=doc.title_zh,
            content_excerpt=excerpt,
        )
        if renamed is not None:
            title_en, title_zh = renamed
            doc.title_en = str(title_en or doc.title_en)[:512]
            doc.title_zh = str(title_zh or doc.title_zh)[:512]
            doc.name_version = "name-v2"
        upsert_bill_fact_for_document(db, doc, content_excerpt=excerpt)

    if out.quality_state == "ok":
        mail_subject = ""
        mail_from = ""
        if source_type == "mail":
            row = (
                db.execute(
                    select(MailIngestionEvent)
                    .where(MailIngestionEvent.attachment_path == str(doc.source_path or ""))
                    .order_by(MailIngestionEvent.created_at.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            if row is not None:
                mail_subject = str(row.subject or "")
                mail_from = str(row.from_addr or "")
        auto_tags = infer_auto_tags(
            file_name=doc.file_name,
            source_path=doc.source_path,
            source_type=source_type,
            summary_en=doc.summary_en,
            summary_zh=doc.summary_zh,
            content_excerpt=excerpt,
            category_path=doc.category_path,
            mail_from=mail_from,
            mail_subject=mail_subject,
        )
        crud.sync_auto_tags_for_document(db, document_id=doc.id, auto_tag_keys=auto_tags)
        tags_recomputed = True
        doc_tags = crud.get_document_tag_keys(db, doc.id)
        doc.updated_at = dt.datetime.now(dt.UTC)

        chunks_for_vector = db.execute(select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.chunk_index.asc())).scalars().all()
        payload_records = [
            qdrant_payload(
                doc_id=doc.id,
                chunk_id=chunk.id,
                doc_lang=doc.doc_lang,
                category_path=doc.category_path,
                source_type=source_type,
                updated_at=doc.updated_at,
                title_en=doc.title_en,
                title_zh=doc.title_zh,
                tags=doc_tags,
                text=chunk.content,
            )
            for chunk in chunks_for_vector
        ]
        if payload_records:
            try:
                upsert_records(payload_records)
                qdrant_synced = True
            except Exception as exc:
                qdrant_synced = False
                logger.warning(
                    "qdrant_upsert_error",
                    extra=sanitize_log_context({"doc_id": doc.id, "error_code": "qdrant_upsert_error", "detail": str(exc)}),
                )
        else:
            qdrant_synced = True

    db.commit()
    db.refresh(doc)
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
