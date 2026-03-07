from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud
from app.models import Chunk, Document, DocumentStatus
from app.schemas import AgentRelatedDoc


def _dedupe_hits_by_chunk(hits: list[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for hit in hits:
        cid = str(getattr(hit, "chunk_id", "") or "").strip()
        if (not cid) or (cid in seen):
            continue
        seen.add(cid)
        out.append(hit)
    return out


def _build_related_docs(
    db: Session, doc_ids: list[str], *, cap: int = 6
) -> list[AgentRelatedDoc]:
    unique_ids: list[str] = []
    seen: set[str] = set()
    for raw in doc_ids:
        doc_id = str(raw or "").strip()
        if (not doc_id) or (doc_id in seen):
            continue
        seen.add(doc_id)
        unique_ids.append(doc_id)
    if not unique_ids:
        return []

    rows = (
        db.execute(
            select(Document).where(
                Document.id.in_(unique_ids),
                Document.status == DocumentStatus.COMPLETED.value,
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return []

    by_id = {str(item.id): item for item in rows}
    ordered_rows: list[Document] = []
    for doc_id in unique_ids:
        found = by_id.get(doc_id)
        if found is None:
            continue
        ordered_rows.append(found)
        if len(ordered_rows) >= cap:
            break
    if not ordered_rows:
        return []

    tag_map = crud.get_document_tags_map(db, [item.id for item in ordered_rows])
    out: list[AgentRelatedDoc] = []
    for item in ordered_rows:
        source_available = crud.source_path_available(item.source_path)
        out.append(
            AgentRelatedDoc(
                doc_id=item.id,
                file_name=item.file_name,
                title_en=item.title_en,
                title_zh=item.title_zh,
                summary_en=item.summary_en,
                summary_zh=item.summary_zh,
                category_path=item.category_path,
                category_label_en=item.category_label_en,
                category_label_zh=item.category_label_zh,
                tags=tag_map.get(item.id, []),
                source_available=source_available,
                source_missing_reason="" if source_available else "source_file_missing",
                updated_at=item.updated_at,
            )
        )
    return out


def _collect_evidence_backed_doc_ids(bundle: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    detail_sections = list(bundle.get("detail_sections") or [])
    for section in detail_sections:
        for row in list(getattr(section, "rows", []) or []):
            for ev in list(getattr(row, "evidence_refs", []) or []):
                doc_id = str(getattr(ev, "doc_id", "") or "").strip()
                if doc_id and doc_id not in seen:
                    seen.add(doc_id)
                    out.append(doc_id)
    if out:
        return out

    evidence_map = bundle.get("evidence_map") or {}
    if isinstance(evidence_map, dict):
        for refs in evidence_map.values():
            if not isinstance(refs, list):
                continue
            for ev in refs:
                if not isinstance(ev, dict):
                    continue
                doc_id = str(ev.get("doc_id") or "").strip()
                if doc_id and doc_id not in seen:
                    seen.add(doc_id)
                    out.append(doc_id)
    if out:
        return out

    explicit = [
        str(x or "").strip()
        for x in (bundle.get("evidence_backed_doc_ids") or [])
        if str(x or "").strip()
    ]
    for doc_id in explicit:
        if doc_id not in seen:
            seen.add(doc_id)
            out.append(doc_id)
    if out:
        return out

    for chunk in list(bundle.get("context_chunks") or [])[:10]:
        doc_id = str(chunk.get("doc_id") or "").strip()
        if doc_id and doc_id not in seen:
            seen.add(doc_id)
            out.append(doc_id)
    return out


def _apply_related_docs_selection(bundle: dict[str, Any]) -> tuple[str, int]:
    route = str(bundle.get("route") or "")
    related_docs = list(bundle.get("related_docs") or [])
    if route not in {
        "detail_extract",
        "entity_fact_lookup",
        "period_aggregate",
        "bill_attention",
        "bill_monthly_total",
    }:
        bundle["related_doc_selection_mode"] = str(
            bundle.get("related_doc_selection_mode") or "evidence_plus_candidates"
        )
        return (str(bundle["related_doc_selection_mode"]), len(related_docs))

    evidence_doc_ids = _collect_evidence_backed_doc_ids(bundle)
    evidence_set = {doc_id for doc_id in evidence_doc_ids if doc_id}
    if evidence_set:
        related_docs = [
            doc
            for doc in related_docs
            if str(getattr(doc, "doc_id", "") or "") in evidence_set
        ]
    else:
        related_docs = []
    bundle["related_docs"] = related_docs
    bundle["related_doc_selection_mode"] = "evidence_only"
    bundle["evidence_backed_doc_ids"] = evidence_doc_ids
    return ("evidence_only", len(evidence_doc_ids))


def _fill_chunks_from_doc_scope(
    db: Session, doc_ids: list[str], existing_chunk_ids: set[str], cap: int
) -> list[dict[str, Any]]:
    if (not doc_ids) or cap <= 0:
        return []
    docs = (
        db.execute(
            select(Document)
            .where(
                Document.id.in_(doc_ids),
                Document.status == DocumentStatus.COMPLETED.value,
            )
            .order_by(Document.updated_at.desc())
        )
        .scalars()
        .all()
    )
    out: list[dict[str, Any]] = []
    for doc in docs:
        if not crud.source_path_available(doc.source_path):
            continue
        rows = (
            db.execute(
                select(Chunk)
                .where(Chunk.document_id == doc.id)
                .order_by(Chunk.chunk_index.asc())
                .limit(3)
            )
            .scalars()
            .all()
        )
        for row in rows:
            if row.id in existing_chunk_ids:
                continue
            existing_chunk_ids.add(row.id)
            out.append(
                {
                    "doc_id": doc.id,
                    "chunk_id": row.id,
                    "score": 0.0,
                    "title_en": doc.title_en,
                    "title_zh": doc.title_zh,
                    "category_path": doc.category_path,
                    "text": str(row.content or ""),
                }
            )
            if len(out) >= cap:
                return out
    return out
