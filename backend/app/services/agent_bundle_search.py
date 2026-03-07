from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Chunk, Document, DocumentStatus
from app.schemas import (
    AgentExecuteRequest,
    DetailCoverageStats,
    DetailSection,
    PlannerDecision,
    ResultCardSource,
    SearchRequest,
)
from app.services.detail_extract import _detail_rows_from_chunks, _resolve_detail_topic
from app.services.docs import (
    _build_related_docs,
    _dedupe_hits_by_chunk,
    _fill_chunks_from_doc_scope,
)
from app.services.query_policy import (
    _detect_query_facet,
    _domain_category_whitelist,
    _is_historical_fact_query,
    _looks_planned_or_proposal_doc,
    _query_required_terms,
)
from app.services.search import search_documents


def _build_detail_extract_bundle(
    db: Session,
    req: AgentExecuteRequest,
    planner: PlannerDecision,
    *,
    doc_ids: list[str],
    category_path: str | None,
) -> dict[str, Any]:
    lowered_query = str(req.query or "").lower()
    topic = _resolve_detail_topic(req.query, planner.doc_scope if isinstance(planner.doc_scope, dict) else {})
    selected_ids = [str(x or "").strip() for x in doc_ids if str(x or "").strip()]
    if selected_ids:
        related_docs = _build_related_docs(db, selected_ids, cap=10)
    else:
        seed_bundle = _build_search_bundle(db, req, planner, doc_ids=[], category_path=category_path)
        related_docs = seed_bundle.get("related_docs") or []

    _active_prefixes: tuple[str, ...] = ()
    scoped_docs = related_docs
    if topic == "insurance":
        insurance_prefixes: tuple[str, ...] = (
            "home/insurance",
            "health/insurance",
            "legal/insurance",
        )
        if any(tok in lowered_query for tok in ("pet insurance", "宠物保险")):
            insurance_prefixes = ("home/insurance/pet",)
        elif any(
            tok in lowered_query
            for tok in (
                "car insurance",
                "vehicle insurance",
                "motor insurance",
                "车险",
                "车辆保险",
                # common car brands (CN + EN) — named vehicle implies car insurance context
                "tesla",
                "特斯拉",
                "toyota",
                "丰田",
                "honda",
                "本田",
                "bmw",
                "宝马",
                "mercedes",
                "奔驰",
                "audi",
                "奥迪",
                "ford",
                "福特",
                "hyundai",
                "现代",
                "mazda",
                "马自达",
                "subaru",
                "斯巴鲁",
                "volkswagen",
                "vw",
                "大众",
                "nissan",
                "日产",
                "kia",
                "起亚",
                "lexus",
                "雷克萨斯",
            )
        ):
            insurance_prefixes = ("home/insurance/vehicle",)
        elif any(
            tok in lowered_query
            for tok in (
                "health insurance",
                "private health",
                "hospital cover",
                "医保",
                "医疗险",
            )
        ):
            insurance_prefixes = ("health/insurance",)
        _active_prefixes = insurance_prefixes
        scoped_docs = [doc for doc in related_docs if str(doc.category_path or "").startswith(insurance_prefixes)]
    elif topic == "bill":
        _active_prefixes = ("finance/bills",)
        scoped_docs = [doc for doc in related_docs if str(doc.category_path or "").startswith("finance/bills")]
    elif topic == "home":
        _active_prefixes = ("home/property", "home/maintenance", "legal/property")
        scoped_docs = [doc for doc in related_docs if str(doc.category_path or "").startswith(_active_prefixes)]
    elif topic == "appliances":
        _active_prefixes = ("home/manuals", "home/appliances", "tech/hardware")
        scoped_docs = [doc for doc in related_docs if str(doc.category_path or "").startswith(_active_prefixes)]
    elif topic == "pets":
        pet_prefixes: tuple[str, ...] = (
            "home/pets",
            "health/medical_records",
            "home/insurance/pet",
        )
        if any(tok in lowered_query for tok in ("birthday", "birth date", "dob", "生日", "出生日期")):
            # Broaden to health/insurance — desexing/medical certs may be filed there
            pet_prefixes = ("home/pets", "health/medical_records", "health/insurance")
        _active_prefixes = pet_prefixes
        scoped_docs = [doc for doc in related_docs if str(doc.category_path or "").startswith(pet_prefixes)]
    elif topic == "warranty":
        _active_prefixes = (
            "home/manuals",
            "home/appliances",
            "tech/hardware",
            "home/maintenance",
        )
        scoped_docs = [doc for doc in related_docs if str(doc.category_path or "").startswith(_active_prefixes)]
    elif topic == "contract":
        _active_prefixes = ("legal/contracts", "legal/property")
        scoped_docs = [doc for doc in related_docs if str(doc.category_path or "").startswith(_active_prefixes)]
    if (not scoped_docs) and topic == "generic":
        scoped_docs = related_docs[:6]
    elif not scoped_docs and _active_prefixes:
        # Vector search found nothing for this topic — fall back to direct category DB query.
        _fb_ids: list[str] = []
        _seen_fb: set[str] = set()
        for _prefix in _active_prefixes:
            _fb_rows = (
                db.execute(
                    select(Document.id)
                    .where(
                        Document.category_path.startswith(_prefix),
                        Document.status == DocumentStatus.COMPLETED.value,
                    )
                    .limit(8)
                )
                .scalars()
                .all()
            )
            for _row in _fb_rows:
                _k = str(_row)
                if _k not in _seen_fb:
                    _fb_ids.append(_k)
                    _seen_fb.add(_k)
            if len(_fb_ids) >= 8:
                break
        if _fb_ids:
            scoped_docs = _build_related_docs(db, _fb_ids, cap=8)

    context_chunks: list[dict[str, Any]] = []
    sources: list[ResultCardSource] = []
    docs_scanned = len(scoped_docs)
    docs_matched = 0
    for doc in scoped_docs[:8]:
        if len(context_chunks) >= 10:
            break
        rows = (
            db.execute(select(Chunk).where(Chunk.document_id == doc.doc_id).order_by(Chunk.chunk_index.asc()).limit(4))
            .scalars()
            .all()
        )
        if not rows:
            continue
        docs_matched += 1
        for row in rows:
            context_chunks.append(
                {
                    "doc_id": doc.doc_id,
                    "chunk_id": row.id,
                    "score": 0.6,
                    "title_en": doc.title_en,
                    "title_zh": doc.title_zh,
                    "category_path": doc.category_path,
                    "text": str(row.content or ""),
                }
            )
        sources.append(
            ResultCardSource(
                doc_id=doc.doc_id,
                chunk_id=str(rows[0].id),
                label=str(doc.title_zh or doc.title_en or doc.file_name),
            )
        )

    _howto_tokens = (
        "方法",
        "如何",
        "怎么",
        "步骤",
        "怎样",
        "how to",
        "how do",
        "how can",
        "what steps",
        "维护方法",
        "使用方法",
        "操作方法",
    )
    _is_howto = any(tok in lowered_query for tok in _howto_tokens)
    if _is_howto and topic in {"generic", "home", "appliances"}:
        detail_rows, missing_fields = [], []
    else:
        detail_rows, missing_fields = _detail_rows_from_chunks(topic=topic, chunks=context_chunks, ui_lang=req.ui_lang)
    fields_filled = sum(1 for row in detail_rows if str(row.value_zh or row.value_en).strip())
    detail_sections = [DetailSection(section_name=f"{topic}_details", rows=detail_rows)] if detail_rows else []
    evidence_doc_ids: list[str] = []
    seen_evidence_docs: set[str] = set()
    for row in detail_rows:
        for ev in list(getattr(row, "evidence_refs", []) or []):
            doc_id = str(getattr(ev, "doc_id", "") or "").strip()
            if (not doc_id) or (doc_id in seen_evidence_docs):
                continue
            seen_evidence_docs.add(doc_id)
            evidence_doc_ids.append(doc_id)
    if evidence_doc_ids:
        scoped_docs = [doc for doc in scoped_docs if str(doc.doc_id or "") in seen_evidence_docs]
    return {
        "route": "detail_extract",
        "context_chunks": context_chunks[:12],
        "sources": sources[:8],
        "related_docs": scoped_docs[:8],
        "hit_count": len(context_chunks),
        "doc_count": len(scoped_docs[:8]),
        "query_en": "",
        "bilingual_search": False,
        "qdrant_used": False,
        "retrieval_mode": "structured",
        "vector_hit_count": 0,
        "lexical_hit_count": 0,
        "fallback_reason": "detail_zero_hit" if not context_chunks else "",
        "detail_topic": topic,
        "detail_mode": "structured",
        "detail_rows_count": len(detail_rows),
        "detail_sections": detail_sections,
        "missing_fields": missing_fields,
        "coverage_stats": DetailCoverageStats(
            docs_scanned=int(docs_scanned),
            docs_matched=int(docs_matched),
            fields_filled=int(fields_filled),
        ),
        "related_doc_selection_mode": "evidence_only" if evidence_doc_ids else "evidence_plus_candidates",
        "evidence_backed_doc_ids": evidence_doc_ids,
    }


def _build_entity_fact_lookup_bundle(
    db: Session,
    req: AgentExecuteRequest,
    planner: PlannerDecision,
    *,
    doc_ids: list[str],
    category_path: str | None,
) -> dict[str, Any]:
    # Reuse generic detail extraction templates, but expose route as entity_fact_lookup
    # so routing/eval can audit structured usage.
    out = _build_detail_extract_bundle(db, req, planner, doc_ids=doc_ids, category_path=category_path)
    out["route"] = "entity_fact_lookup"
    out["route_reason"] = "entity_fact_structured"
    out["fact_route"] = "none"
    out["fact_month"] = ""
    return out


def _build_search_bundle(
    db: Session,
    req: AgentExecuteRequest,
    planner: PlannerDecision,
    *,
    doc_ids: list[str],
    category_path: str | None,
) -> dict[str, Any]:
    facet = _detect_query_facet(req.query)
    domain_whitelist = tuple(path.lower() for path in _domain_category_whitelist(req.query, facet))
    query_required_terms = _query_required_terms(req.query)
    historical_fact_query = _is_historical_fact_query(req.query)
    strict_categories = {str(item or "").strip().lower() for item in facet.strict_categories if str(item or "").strip()}
    required_terms = [str(item or "").strip().lower() for item in facet.required_terms if str(item or "").strip()]

    effective_category_path = category_path
    if (not effective_category_path) and facet.strict_mode and len(strict_categories) == 1:
        effective_category_path = next(iter(strict_categories))

    search_req = SearchRequest(
        query=req.query,
        top_k=12,
        score_threshold=0.0,
        ui_lang=planner.ui_lang if planner.ui_lang in {"zh", "en"} else ("zh" if req.ui_lang == "zh" else "en"),
        query_lang=planner.query_lang if planner.query_lang in {"zh", "en"} else req.query_lang,
        category_path=effective_category_path,
        include_missing=False,
    )
    search_res = search_documents(db, search_req)
    hits = _dedupe_hits_by_chunk(search_res.hits)
    if doc_ids:
        allowed_doc_ids = set(doc_ids)
        hits = [hit for hit in hits if str(hit.doc_id) in allowed_doc_ids]

    candidate_doc_ids = [str(hit.doc_id or "").strip() for hit in hits if str(hit.doc_id or "").strip()]
    candidate_docs = db.execute(select(Document).where(Document.id.in_(set(candidate_doc_ids)))).scalars().all()
    doc_map = {str(item.id): item for item in candidate_docs}

    top_hits = hits[:10]
    hit_chunk_ids = [str(hit.chunk_id or "").strip() for hit in top_hits if str(hit.chunk_id or "").strip()]
    chunk_rows = (
        db.execute(select(Chunk).where(Chunk.id.in_(set(hit_chunk_ids)))).scalars().all() if hit_chunk_ids else []
    )
    chunk_map = {str(chunk.id): chunk for chunk in chunk_rows}

    context_chunks: list[dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()
    doc_best_score: dict[str, float] = {}
    doc_first_seen: dict[str, int] = {}
    for hit in top_hits:
        chunk = chunk_map.get(str(hit.chunk_id or "").strip())
        if chunk is None:
            continue
        if chunk.id in seen_chunk_ids:
            continue
        doc_id = str(hit.doc_id or "").strip()
        if not doc_id:
            continue

        if facet.strict_mode:
            hit_category = str(hit.category_path or "").strip().lower()
            if strict_categories and hit_category not in strict_categories:
                continue
            doc = doc_map.get(doc_id)
            text_blob = " ".join(
                [
                    str(hit.title_zh or ""),
                    str(hit.title_en or ""),
                    str(hit.category_path or ""),
                    str(getattr(doc, "file_name", "") or ""),
                    str(getattr(doc, "summary_zh", "") or ""),
                    str(getattr(doc, "summary_en", "") or ""),
                    " ".join(str(item or "") for item in (getattr(hit, "tags", []) or [])),
                    str(chunk.content or ""),
                ]
            ).lower()
            if required_terms and (not any(term in text_blob for term in required_terms)):
                continue
            if historical_fact_query and _looks_planned_or_proposal_doc(text_blob):
                continue
        else:
            text_blob = ""
            if domain_whitelist or query_required_terms:
                hit_category = str(hit.category_path or "").strip().lower()
                if domain_whitelist and (not any(hit_category.startswith(path) for path in domain_whitelist)):
                    continue
                doc = doc_map.get(doc_id)
                text_blob = " ".join(
                    [
                        str(hit.title_zh or ""),
                        str(hit.title_en or ""),
                        str(hit.category_path or ""),
                        str(getattr(doc, "file_name", "") or ""),
                        str(getattr(doc, "summary_zh", "") or ""),
                        str(getattr(doc, "summary_en", "") or ""),
                        str(chunk.content or ""),
                    ]
                ).lower()
            if query_required_terms and (not any(term in text_blob for term in query_required_terms)):
                continue
            if historical_fact_query and text_blob and _looks_planned_or_proposal_doc(text_blob):
                continue

        seen_chunk_ids.add(chunk.id)
        context_chunks.append(
            {
                "doc_id": doc_id,
                "chunk_id": str(hit.chunk_id),
                "score": float(hit.score),
                "title_en": str(hit.title_en or ""),
                "title_zh": str(hit.title_zh or ""),
                "category_path": str(hit.category_path or ""),
                "text": str(chunk.content or ""),
            }
        )
        if doc_id not in doc_first_seen:
            doc_first_seen[doc_id] = len(doc_first_seen)
        current = float(doc_best_score.get(doc_id, 0.0))
        doc_best_score[doc_id] = max(current, float(hit.score or 0.0))

    if facet.strict_mode and (not context_chunks):
        return {
            "route": "search_bundle",
            "context_chunks": [],
            "sources": [],
            "related_docs": [],
            "hit_count": 0,
            "doc_count": 0,
            "query_en": str(search_res.query_en or ""),
            "bilingual_search": bool(search_res.bilingual),
            "qdrant_used": bool(search_res.qdrant_used),
            "retrieval_mode": str(search_res.retrieval_mode or "none"),
            "vector_hit_count": int(search_res.vector_hit_count or 0),
            "lexical_hit_count": int(search_res.lexical_hit_count or 0),
            "fallback_reason": "strict_filter_zero_hit",
            "facet_mode": "strict_topic",
            "facet_keys": list(facet.facet_keys),
            "related_doc_selection_mode": "evidence_only",
            "evidence_backed_doc_ids": [],
        }

    if (not facet.strict_mode) and domain_whitelist and (not context_chunks):
        return {
            "route": "search_bundle",
            "context_chunks": [],
            "sources": [],
            "related_docs": [],
            "hit_count": 0,
            "doc_count": 0,
            "query_en": str(search_res.query_en or ""),
            "bilingual_search": bool(search_res.bilingual),
            "qdrant_used": bool(search_res.qdrant_used),
            "retrieval_mode": str(search_res.retrieval_mode or "none"),
            "vector_hit_count": int(search_res.vector_hit_count or 0),
            "lexical_hit_count": int(search_res.lexical_hit_count or 0),
            "fallback_reason": "domain_filter_zero_hit",
            "facet_mode": "none",
            "facet_keys": [],
            "related_doc_selection_mode": "evidence_only",
            "evidence_backed_doc_ids": [],
        }

    if (not facet.strict_mode) and query_required_terms and (not context_chunks):
        return {
            "route": "search_bundle",
            "context_chunks": [],
            "sources": [],
            "related_docs": [],
            "hit_count": 0,
            "doc_count": 0,
            "query_en": str(search_res.query_en or ""),
            "bilingual_search": bool(search_res.bilingual),
            "qdrant_used": bool(search_res.qdrant_used),
            "retrieval_mode": str(search_res.retrieval_mode or "none"),
            "vector_hit_count": int(search_res.vector_hit_count or 0),
            "lexical_hit_count": int(search_res.lexical_hit_count or 0),
            "fallback_reason": "query_qualifier_zero_hit",
            "facet_mode": "none",
            "facet_keys": [],
            "query_required_terms": query_required_terms,
            "related_doc_selection_mode": "evidence_only",
            "evidence_backed_doc_ids": [],
        }

    if (not facet.strict_mode) and len(context_chunks) < 3:
        need = max(0, 3 - len(context_chunks))
        context_chunks.extend(_fill_chunks_from_doc_scope(db, doc_ids, seen_chunk_ids, need))
    context_chunks = context_chunks[:10]

    sources: list[ResultCardSource] = []
    source_doc_ids: list[str] = []
    for item in context_chunks[:5]:
        label = item.get("title_zh") if req.ui_lang == "zh" else item.get("title_en")
        if not label:
            label = item.get("title_en") or item.get("title_zh") or "Document"
        sources.append(
            ResultCardSource(
                doc_id=str(item.get("doc_id") or ""),
                chunk_id=str(item.get("chunk_id") or ""),
                label=str(label),
            )
        )
        doc_id = str(item.get("doc_id") or "")
        if doc_id:
            source_doc_ids.append(doc_id)

    ordered_doc_ids = sorted(
        doc_best_score.keys(),
        key=lambda key: (
            -float(doc_best_score.get(key, 0.0)),
            int(doc_first_seen.get(key, 10**6)),
        ),
    )
    if ordered_doc_ids:
        source_doc_ids = ordered_doc_ids
    related_docs = _build_related_docs(db, source_doc_ids, cap=6)
    return {
        "route": "search_bundle",
        "context_chunks": context_chunks,
        "sources": sources,
        "related_docs": related_docs,
        "hit_count": len(context_chunks),
        "doc_count": len({str(item.get("doc_id") or "") for item in context_chunks if str(item.get("doc_id") or "")}),
        "query_en": str(search_res.query_en or ""),
        "bilingual_search": bool(search_res.bilingual),
        "qdrant_used": bool(search_res.qdrant_used),
        "retrieval_mode": str(search_res.retrieval_mode or "none"),
        "vector_hit_count": int(search_res.vector_hit_count or 0),
        "lexical_hit_count": int(search_res.lexical_hit_count or 0),
        "fallback_reason": "",
        "facet_mode": "strict_topic" if facet.strict_mode else "none",
        "facet_keys": list(facet.facet_keys),
        "query_required_terms": query_required_terms,
        "related_doc_selection_mode": "evidence_plus_candidates",
    }
