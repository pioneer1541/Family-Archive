import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud
from app.models import Document, IngestionJob
from app.schemas import AgentExecuteRequest, ResultCardSource
from app.services.docs import _build_related_docs
from app.services.ingestion import enqueue_ingestion_job

_TAG_KEY_RE = re.compile(r"\b([a-z0-9][a-z0-9._-]{0,31}:[a-z0-9][a-z0-9._-]{0,95})\b")


def _build_queue_bundle(db: Session, req: AgentExecuteRequest) -> dict[str, Any]:
    totals = crud.get_queue_totals(db)
    jobs = (
        db.execute(
            select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(6)
        )
        .scalars()
        .all()
    )
    docs = (
        db.execute(select(Document).order_by(Document.updated_at.desc()).limit(10))
        .scalars()
        .all()
    )
    docs = [item for item in docs if crud.source_path_available(item.source_path)]

    context_chunks: list[dict[str, Any]] = [
        {
            "doc_id": "",
            "chunk_id": "queue-totals",
            "score": 1.0,
            "title_en": "Queue Totals",
            "title_zh": "队列统计",
            "category_path": "system/queue",
            "text": (
                f"documents={int(totals.get('documents') or 0)}, "
                f"pending_documents={int(totals.get('pending_documents') or 0)}, "
                f"jobs={int(totals.get('jobs') or 0)}, "
                f"running_jobs={int(totals.get('running_jobs') or 0)}"
            ),
        }
    ]
    for idx, job in enumerate(jobs[:5], start=1):
        context_chunks.append(
            {
                "doc_id": "",
                "chunk_id": f"queue-job-{idx}",
                "score": 0.9,
                "title_en": "Ingestion Job",
                "title_zh": "入库任务",
                "category_path": "system/queue",
                "text": (
                    f"job_id={job.id}, status={job.status}, success={job.success_count}, failed={job.failed_count}, "
                    f"duplicate={job.duplicate_count}"
                ),
            }
        )

    related_docs = _build_related_docs(db, [doc.id for doc in docs], cap=4)
    sources: list[ResultCardSource] = [
        ResultCardSource(
            doc_id=item.doc_id,
            chunk_id=f"doc-ref-{idx + 1}",
            label=item.title_zh or item.title_en or item.file_name,
        )
        for idx, item in enumerate(related_docs)
    ]
    return {
        "route": "queue_snapshot",
        "context_chunks": context_chunks[:10],
        "sources": sources[:5],
        "related_docs": related_docs,
        "hit_count": len(context_chunks),
        "doc_count": len(related_docs),
        "query_en": "",
        "bilingual_search": False,
        "qdrant_used": False,
        "retrieval_mode": "structured",
        "vector_hit_count": 0,
        "lexical_hit_count": 0,
        "fallback_reason": "",
        "queue_totals": totals,
    }


def _extract_tag_keys_from_query(query: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for matched in _TAG_KEY_RE.findall(str(query or "").lower()):
        key = str(matched or "").strip()
        if (not key) or (key in seen):
            continue
        seen.add(key)
        out.append(key)
    return out[:12]


def _build_reprocess_bundle(
    db: Session, req: AgentExecuteRequest, *, doc_ids: list[str]
) -> dict[str, Any]:
    target_ids = doc_ids[:3]
    if not target_ids:
        return {
            "route": "reprocess_exec",
            "context_chunks": [
                {
                    "doc_id": "",
                    "chunk_id": "reprocess-empty",
                    "score": 0.1,
                    "title_en": "Reprocess",
                    "title_zh": "重处理",
                    "category_path": "system/reprocess",
                    "text": "No selected document IDs were provided for reprocess.",
                }
            ],
            "sources": [],
            "related_docs": [],
            "hit_count": 0,
            "doc_count": 0,
            "query_en": "",
            "bilingual_search": False,
            "qdrant_used": False,
            "retrieval_mode": "structured",
            "vector_hit_count": 0,
            "lexical_hit_count": 0,
            "fallback_reason": "no_selected_docs",
        }

    context_chunks: list[dict[str, Any]] = []
    related_doc_ids: list[str] = []
    for idx, doc_id in enumerate(target_ids, start=1):
        doc = db.get(Document, doc_id)
        if doc is None:
            context_chunks.append(
                {
                    "doc_id": "",
                    "chunk_id": f"reprocess-missing-{idx}",
                    "score": 0.1,
                    "title_en": "Reprocess",
                    "title_zh": "重处理",
                    "category_path": "system/reprocess",
                    "text": f"document_not_found: {doc_id}",
                }
            )
            continue
        related_doc_ids.append(doc.id)
        if not str(doc.source_path or "").strip():
            context_chunks.append(
                {
                    "doc_id": doc.id,
                    "chunk_id": f"reprocess-nosource-{idx}",
                    "score": 0.2,
                    "title_en": doc.title_en,
                    "title_zh": doc.title_zh,
                    "category_path": doc.category_path,
                    "text": f"reprocess_skipped: {doc.file_name}, reason=document_has_no_source_path",
                }
            )
            continue
        job = crud.create_ingestion_job(db, [str(doc.source_path)])
        mode = enqueue_ingestion_job(
            job.id, force_reprocess=True, reprocess_doc_id=doc.id
        )
        context_chunks.append(
            {
                "doc_id": doc.id,
                "chunk_id": f"reprocess-job-{job.id}",
                "score": 0.95,
                "title_en": doc.title_en,
                "title_zh": doc.title_zh,
                "category_path": doc.category_path,
                "text": f"reprocess_queued: file={doc.file_name}, job_id={job.id}, queue_mode={mode}",
            }
        )

    related_docs = _build_related_docs(db, related_doc_ids, cap=4)
    sources = [
        ResultCardSource(
            doc_id=item.doc_id,
            chunk_id=f"reprocess-ref-{idx + 1}",
            label=item.title_zh or item.title_en or item.file_name,
        )
        for idx, item in enumerate(related_docs)
    ]
    return {
        "route": "reprocess_exec",
        "context_chunks": context_chunks[:10],
        "sources": sources[:5],
        "related_docs": related_docs,
        "hit_count": len(context_chunks),
        "doc_count": len(related_docs),
        "query_en": "",
        "bilingual_search": False,
        "qdrant_used": False,
        "retrieval_mode": "structured",
        "vector_hit_count": 0,
        "lexical_hit_count": 0,
        "fallback_reason": "",
    }


def _build_tag_update_bundle(
    db: Session, req: AgentExecuteRequest, *, doc_ids: list[str]
) -> dict[str, Any]:
    target_ids = doc_ids[:3]
    tag_keys = _extract_tag_keys_from_query(req.query)
    context_chunks: list[dict[str, Any]] = []
    related_doc_ids: list[str] = []
    updated = 0

    for idx, doc_id in enumerate(target_ids, start=1):
        doc = db.get(Document, doc_id)
        if doc is None:
            continue
        related_doc_ids.append(doc.id)
        if not tag_keys:
            context_chunks.append(
                {
                    "doc_id": doc.id,
                    "chunk_id": f"tag-suggest-{idx}",
                    "score": 0.3,
                    "title_en": doc.title_en,
                    "title_zh": doc.title_zh,
                    "category_path": doc.category_path,
                    "text": f"tag_update_noop: file={doc.file_name}, reason=no_tag_keys_found",
                }
            )
            continue
        _rows, invalid = crud.patch_document_tags(
            db, document_id=doc.id, add=tag_keys, remove=[]
        )
        if invalid:
            context_chunks.append(
                {
                    "doc_id": doc.id,
                    "chunk_id": f"tag-invalid-{idx}",
                    "score": 0.2,
                    "title_en": doc.title_en,
                    "title_zh": doc.title_zh,
                    "category_path": doc.category_path,
                    "text": f"tag_update_failed: file={doc.file_name}, reason={','.join(invalid)}",
                }
            )
            continue
        updated += 1
        context_chunks.append(
            {
                "doc_id": doc.id,
                "chunk_id": f"tag-ok-{idx}",
                "score": 0.9,
                "title_en": doc.title_en,
                "title_zh": doc.title_zh,
                "category_path": doc.category_path,
                "text": f"tag_update_applied: file={doc.file_name}, add={','.join(tag_keys)}",
            }
        )

    if updated > 0:
        db.commit()
    related_docs = _build_related_docs(db, related_doc_ids, cap=4)
    sources = [
        ResultCardSource(
            doc_id=item.doc_id,
            chunk_id=f"tag-ref-{idx + 1}",
            label=item.title_zh or item.title_en or item.file_name,
        )
        for idx, item in enumerate(related_docs)
    ]
    if not context_chunks:
        context_chunks = [
            {
                "doc_id": "",
                "chunk_id": "tag-empty",
                "score": 0.1,
                "title_en": "Tag Update",
                "title_zh": "标签更新",
                "category_path": "system/tags",
                "text": "No selected documents found for tag update.",
            }
        ]
    return {
        "route": "tag_update_exec",
        "context_chunks": context_chunks[:10],
        "sources": sources[:5],
        "related_docs": related_docs,
        "hit_count": len(context_chunks),
        "doc_count": len(related_docs),
        "query_en": "",
        "bilingual_search": False,
        "qdrant_used": False,
        "retrieval_mode": "structured",
        "vector_hit_count": 0,
        "lexical_hit_count": 0,
        "fallback_reason": "",
    }
