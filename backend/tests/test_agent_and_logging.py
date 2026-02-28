import datetime as dt
from pathlib import Path
from types import SimpleNamespace

from app.logging_utils import sanitize_log_context
from app.schemas import PlannerRequest
from app.db import SessionLocal
from app.models import Chunk, Document
from app.services import planner as planner_service
from app.services import agent as agent_service


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_agent_plan_and_execute(client, tmp_path: Path):
    sample = tmp_path / "timeline.txt"
    sample.write_text("2025-01 buy insurance. 2025-03 claim lodged.", encoding="utf-8")
    client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})

    rp = client.post("/v1/agent/plan", json={"query": "请总结这个文档", "ui_lang": "zh", "query_lang": "zh", "doc_scope": {}})
    assert rp.status_code == 200
    planner = rp.json()
    assert "intent" in planner
    assert "confidence" in planner

    re = client.post("/v1/agent/execute", json={"query": "总结保险事件", "ui_lang": "zh", "query_lang": "zh"})
    assert re.status_code == 200
    data = re.json()
    assert "card" in data
    assert "short_summary" in data["card"]
    assert "en" in data["card"]["short_summary"]
    assert "zh" in data["card"]["short_summary"]


def test_planner_uses_llm_json_when_available(monkeypatch):
    payload = {
        "message": {
            "content": (
                '{"intent":"summarize_docs","confidence":0.81,'
                '"doc_scope":{"doc_ids":["doc-1"]},"actions":["retrieve_docs","summarize_docs"],'
                '"fallback":"summarize_docs","ui_lang":"zh","query_lang":"zh"}'
            )
        }
    }
    monkeypatch.setattr(planner_service.requests, "post", lambda *args, **kwargs: _FakeResp(payload))
    decision = planner_service.plan_from_request(
        PlannerRequest(query="总结最近账单", ui_lang="zh", query_lang="auto", doc_scope={"doc_ids": ["doc-1"]})
    )
    assert decision.intent == "summarize_docs"
    assert float(decision.confidence) >= 0.8
    assert decision.doc_scope.get("doc_ids") == ["doc-1"]
    assert "summarize_docs" in decision.actions


def test_planner_fallback_routes_for_entity_and_period(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("planner_llm_unavailable")

    monkeypatch.setattr(planner_service.requests, "post", _raise)
    entity = planner_service.plan_from_request(
        PlannerRequest(query="空调型号是什么？", ui_lang="zh", query_lang="auto", doc_scope={})
    )
    period = planner_service.plan_from_request(
        PlannerRequest(query="过去六个月电费平均是多少？", ui_lang="zh", query_lang="auto", doc_scope={})
    )
    assert entity.intent == "entity_fact_lookup"
    assert period.intent == "period_aggregate"


def test_planner_fallback_routes_for_english_queries(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("planner_llm_unavailable")

    monkeypatch.setattr(planner_service.requests, "post", _raise)
    birthday = planner_service.plan_from_request(
        PlannerRequest(query="Fluffy's birthday", ui_lang="en", query_lang="auto", doc_scope={})
    )
    current_bills = planner_service.plan_from_request(
        PlannerRequest(query="current bills", ui_lang="en", query_lang="auto", doc_scope={})
    )
    how_to = planner_service.plan_from_request(
        PlannerRequest(query="how to maintain our water tank", ui_lang="en", query_lang="auto", doc_scope={})
    )
    assert birthday.intent == "entity_fact_lookup"
    assert current_bills.intent == "list_recent"
    assert how_to.intent == "detail_extract"


def test_planner_overrides_low_confidence_wrong_intent_for_english_howto(monkeypatch):
    payload = {
        "message": {
            "content": (
                '{"intent":"compare_docs","confidence":0.55,'
                '"doc_scope":{},"actions":["compare_docs"],'
                '"fallback":"search_semantic","ui_lang":"en","query_lang":"en","route_reason":"llm_plan"}'
            )
        }
    }
    monkeypatch.setattr(planner_service.requests, "post", lambda *args, **kwargs: _FakeResp(payload))
    decision = planner_service.plan_from_request(
        PlannerRequest(query="how to maintain our water tank", ui_lang="en", query_lang="auto", doc_scope={})
    )
    assert decision.intent == "detail_extract"
    assert decision.route_reason == "heuristic_intent_override"


def test_agent_execute_respects_doc_scope_filter(client, tmp_path: Path, monkeypatch):
    a = tmp_path / "scope_a.txt"
    b = tmp_path / "scope_b.txt"
    a.write_text("commonword finance and utilities data for doc A", encoding="utf-8")
    b.write_text("commonword property and maintenance data for doc B", encoding="utf-8")
    client.post("/v1/ingestion/jobs", json={"file_paths": [str(a)]})
    client.post("/v1/ingestion/jobs", json={"file_paths": [str(b)]})

    docs = client.get("/v1/documents?limit=50&offset=0&status=completed")
    assert docs.status_code == 200
    items = docs.json().get("items") or []
    doc_a = next(item["doc_id"] for item in items if item["file_name"] == "scope_a.txt")

    llm_payload = {
        "message": {
            "content": (
                '{"intent":"summarize_docs","confidence":0.77,'
                '"doc_scope":{"doc_ids":["' + doc_a + '"]},"actions":["retrieve_docs","summarize_docs"],'
                '"fallback":"summarize_docs","ui_lang":"en","query_lang":"en"}'
            )
        }
    }
    monkeypatch.setattr(planner_service.requests, "post", lambda *args, **kwargs: _FakeResp(llm_payload))

    r = client.post(
        "/v1/agent/execute",
        json={"query": "commonword", "ui_lang": "en", "query_lang": "en", "doc_scope": {"doc_ids": [doc_a]}},
    )
    assert r.status_code == 200
    out = r.json()
    sources = out.get("card", {}).get("sources") or []
    assert len(sources) >= 1
    assert all(str(src.get("doc_id") or "") == doc_a for src in sources)


def test_log_sanitization_rules():
    out = sanitize_log_context(
        {
            "doc_id": "doc-1",
            "email": "alice@example.com",
            "address": "100 Main Street",
            "note": "contact bob@example.com with account 123456789012",
            "chunk": "secret raw text",
        }
    )
    assert out["doc_id"] == "doc-1"
    assert out["email"] == "[REDACTED]"
    assert out["address"] == "[REDACTED]"
    assert "[REDACTED_EMAIL]" in out["note"]
    assert "[REDACTED_ACCOUNT]" in out["note"]
    assert out["chunk"] == "[REDACTED]"


def test_agent_bill_attention_returns_related_docs_and_stats(client, tmp_path: Path, monkeypatch):
    water = tmp_path / "water_bill_2024_12.txt"
    power = tmp_path / "electric_bill_2024_12.txt"
    water.write_text("Water bill amount due $52.50 due date 2025-01-20 paid", encoding="utf-8")
    power.write_text("Electricity tax invoice amount due $289.90 due date 2025-01-15 unpaid", encoding="utf-8")
    client.post("/v1/ingestion/jobs", json={"file_paths": [str(water)]})
    client.post("/v1/ingestion/jobs", json={"file_paths": [str(power)]})

    with SessionLocal() as db:
        docs = db.query(Document).all()
        assert len(docs) >= 2
        docs = docs[:2]
        for doc in docs:
            doc.status = "completed"
            doc.category_path = "finance/bills/electricity" if "electric" in doc.file_name else "finance/bills/water"
            doc.category_label_en = "Bills"
            doc.category_label_zh = "账单"
        db.commit()
        doc_a = SimpleNamespace(
            id=docs[0].id,
            file_name=docs[0].file_name,
            source_path=docs[0].source_path,
            title_en=docs[0].title_en,
            title_zh=docs[0].title_zh,
            category_path=docs[0].category_path,
        )
        doc_b = SimpleNamespace(
            id=docs[1].id,
            file_name=docs[1].file_name,
            source_path=docs[1].source_path,
            title_en=docs[1].title_en,
            title_zh=docs[1].title_zh,
            category_path=docs[1].category_path,
        )

    fake_rows = [
        (
            SimpleNamespace(amount_due=289.90, currency="AUD", due_date=None, payment_status="unpaid", confidence=0.91),
            doc_b,
        ),
        (
            SimpleNamespace(amount_due=52.50, currency="AUD", due_date=None, payment_status="paid", confidence=0.88),
            doc_a,
        ),
    ]
    monkeypatch.setattr(agent_service, "list_recent_bill_facts", lambda *args, **kwargs: fake_rows)

    r = client.post(
        "/v1/agent/execute",
        json={
            "query": "最近有哪些账单需要关注？",
            "ui_lang": "zh",
            "query_lang": "zh",
            "planner": {
                "intent": "list_recent",
                "confidence": 0.91,
                "doc_scope": {},
                "actions": ["list_recent"],
                "fallback": "search_semantic",
                "ui_lang": "zh",
                "query_lang": "zh",
            },
        },
    )
    assert r.status_code == 200
    out = r.json()
    assert out.get("trace_id")
    assert out.get("executor_stats", {}).get("route") == "bill_attention"
    assert out.get("executor_stats", {}).get("qdrant_used") is False
    assert out.get("executor_stats", {}).get("retrieval_mode") == "structured"
    assert int(out.get("executor_stats", {}).get("vector_hit_count") or 0) == 0
    assert int(out.get("executor_stats", {}).get("lexical_hit_count") or 0) == 0
    assert str(out.get("executor_stats", {}).get("fallback_reason") or "") == ""
    assert int(out.get("executor_stats", {}).get("doc_count") or 0) >= 1
    assert len(out.get("related_docs") or []) >= 1


def test_agent_structured_intents_do_not_call_search(client, monkeypatch):
    calls = {"search": 0}

    def _fake_search(*args, **kwargs):
        calls["search"] += 1
        raise AssertionError("search should not be called for structured intents")

    monkeypatch.setattr(agent_service, "search_documents", _fake_search)
    monkeypatch.setattr(agent_service, "list_recent_bill_facts", lambda *args, **kwargs: [])

    payloads = [
        {"query": "查看队列状态", "ui_lang": "zh", "query_lang": "zh"},
        {"query": "更新标签 vendor:agl", "ui_lang": "zh", "query_lang": "zh"},
        {"query": "重处理文档", "ui_lang": "zh", "query_lang": "zh"},
    ]
    for payload in payloads:
        r = client.post("/v1/agent/execute", json=payload)
        assert r.status_code == 200
        assert str(r.json().get("executor_stats", {}).get("retrieval_mode") or "") == "structured"
        assert r.json().get("executor_stats", {}).get("qdrant_used") is False
    assert calls["search"] == 0


def test_agent_semantic_stats_are_exposed(client, monkeypatch):
    def _fake_search(*args, **kwargs):
        return SimpleNamespace(
            hits=[],
            query_en="warranty terms",
            bilingual=True,
            qdrant_used=True,
            retrieval_mode="hybrid",
            vector_hit_count=4,
            lexical_hit_count=2,
        )

    # Force search_semantic route so search_documents is always called regardless of LLM response.
    def _fake_route(req):
        return SimpleNamespace(
            route="lookup", domain="generic", sub_intent="search_semantic",
            rewritten_query=req.query, time_window_months=None,
            ui_lang=req.ui_lang, query_lang=req.query_lang, route_reason="heuristic",
        )

    monkeypatch.setattr(agent_service, "search_documents", _fake_search)
    monkeypatch.setattr(agent_service, "route_and_rewrite", _fake_route)
    r = client.post("/v1/agent/execute", json={"query": "请帮我查找保修条款", "ui_lang": "zh", "query_lang": "zh"})
    assert r.status_code == 200
    stats = r.json().get("executor_stats") or {}
    assert stats.get("route") == "search_bundle"
    assert stats.get("qdrant_used") is True
    assert stats.get("retrieval_mode") == "hybrid"
    assert int(stats.get("vector_hit_count") or 0) == 4
    assert int(stats.get("lexical_hit_count") or 0) == 2
    assert str(stats.get("fallback_reason") or "") == ""


def test_agent_bill_attention_fallback_reason_when_bill_facts_empty(client, monkeypatch):
    monkeypatch.setattr(agent_service, "list_recent_bill_facts", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        agent_service,
        "search_documents",
        lambda *args, **kwargs: SimpleNamespace(
            hits=[],
            query_en="recent bills",
            bilingual=False,
            qdrant_used=False,
            retrieval_mode="lexical_fallback",
            vector_hit_count=0,
            lexical_hit_count=0,
        ),
    )
    r = client.post(
        "/v1/agent/execute",
        json={
            "query": "最近有哪些账单需要关注？",
            "ui_lang": "zh",
            "query_lang": "zh",
            "planner": {
                "intent": "list_recent",
                "confidence": 0.88,
                "doc_scope": {},
                "actions": ["list_recent"],
                "fallback": "search_semantic",
                "ui_lang": "zh",
                "query_lang": "zh",
            },
        },
    )
    assert r.status_code == 200
    stats = r.json().get("executor_stats") or {}
    assert stats.get("route") == "search_bundle"
    assert stats.get("fallback_reason") == "bill_facts_empty"


def test_agent_bill_attention_acceptance_sections(client, tmp_path: Path, monkeypatch):
    water = tmp_path / "water_bill_focus_2024_12.txt"
    power = tmp_path / "electric_bill_focus_2024_12.txt"
    water.write_text("Water bill amount due $52.50 due date 2025-01-20 paid", encoding="utf-8")
    power.write_text("Electricity invoice amount due $289.90 due date 2025-01-15 unpaid", encoding="utf-8")
    client.post("/v1/ingestion/jobs", json={"file_paths": [str(water)]})
    client.post("/v1/ingestion/jobs", json={"file_paths": [str(power)]})

    with SessionLocal() as db:
        docs = db.query(Document).filter(Document.file_name.in_([water.name, power.name])).all()
        assert len(docs) == 2
        for doc in docs:
            doc.status = "completed"
            if "electric" in doc.file_name:
                doc.category_path = "finance/bills/electricity"
                doc.title_zh = "2024年12月电费账单"
            else:
                doc.category_path = "finance/bills/water"
                doc.title_zh = "2024年12月水费账单"
            doc.category_label_zh = "账单与缴费"
            doc.category_label_en = "Bills"
        db.commit()
        docs_map = {doc.file_name: doc for doc in docs}

    fake_rows = [
        (
            SimpleNamespace(
                amount_due=289.90,
                currency="AUD",
                due_date=dt.datetime(2025, 1, 15, tzinfo=dt.UTC),
                payment_status="unpaid",
                confidence=0.93,
            ),
            SimpleNamespace(
                id=docs_map[power.name].id,
                file_name=docs_map[power.name].file_name,
                source_path=docs_map[power.name].source_path,
                title_en=docs_map[power.name].title_en,
                title_zh=docs_map[power.name].title_zh,
                category_path=docs_map[power.name].category_path,
            ),
        ),
        (
            SimpleNamespace(
                amount_due=52.50,
                currency="AUD",
                due_date=dt.datetime(2025, 1, 20, tzinfo=dt.UTC),
                payment_status="paid",
                confidence=0.89,
            ),
            SimpleNamespace(
                id=docs_map[water.name].id,
                file_name=docs_map[water.name].file_name,
                source_path=docs_map[water.name].source_path,
                title_en=docs_map[water.name].title_en,
                title_zh=docs_map[water.name].title_zh,
                category_path=docs_map[water.name].category_path,
            ),
        ),
    ]
    monkeypatch.setattr(agent_service, "list_recent_bill_facts", lambda *args, **kwargs: fake_rows)


def test_agent_refusal_policy_blocks_specific_claims_when_evidence_insufficient(client, tmp_path: Path):
    sample = tmp_path / "subsidy_hint.txt"
    sample.write_text("政府电费补贴政策介绍，可访问网站了解资格。", encoding="utf-8")
    client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})

    r = client.post(
        "/v1/agent/execute",
        json={"query": "我们有没有申请过政府的电费补贴？", "ui_lang": "zh", "query_lang": "zh"},
    )
    assert r.status_code == 200
    out = r.json()
    stats = out.get("executor_stats") or {}
    assert stats.get("answerability") in {"none", "insufficient"}
    short_zh = str(((out.get("card") or {}).get("short_summary") or {}).get("zh") or "")
    assert "无法给出可靠结论" in short_zh or "缺少足够证据" in short_zh
    assert "http" not in short_zh.lower()


def test_agent_home_aircon_and_property_query_returns_related_docs(client, tmp_path: Path, monkeypatch):
    ac = tmp_path / "Daikin Warranty Lot 41.txt"
    prop = tmp_path / "Owners Corporation Notice Lot 41.txt"
    ac.write_text(
        "Daikin split system air conditioner warranty terms include compressor coverage and service window.",
        encoding="utf-8",
    )
    prop.write_text(
        "Owners corporation notice: property maintenance levy and quarterly strata fee due details.",
        encoding="utf-8",
    )
    client.post("/v1/ingestion/jobs", json={"file_paths": [str(ac)]})
    client.post("/v1/ingestion/jobs", json={"file_paths": [str(prop)]})

    with SessionLocal() as db:
        docs = db.query(Document).filter(Document.file_name.in_([ac.name, prop.name])).all()
        assert len(docs) == 2
        for doc in docs:
            doc.status = "completed"
            if doc.file_name == ac.name:
                doc.category_path = "home/manuals"
                doc.category_label_en = "Manuals"
                doc.category_label_zh = "家电手册"
                doc.title_zh = "大金空调保修说明"
            else:
                doc.category_path = "home/property"
                doc.category_label_en = "Property"
                doc.category_label_zh = "物业资料"
                doc.title_zh = "物业费与维护通知"
        db.commit()
        doc_map = {doc.file_name: doc for doc in docs}

        ac_chunk = (
            db.query(Chunk)
            .filter(Chunk.document_id == doc_map[ac.name].id)
            .order_by(Chunk.chunk_index.asc())
            .first()
        )
        prop_chunk = (
            db.query(Chunk)
            .filter(Chunk.document_id == doc_map[prop.name].id)
            .order_by(Chunk.chunk_index.asc())
            .first()
        )
        assert ac_chunk is not None
        assert prop_chunk is not None

    def _fake_search(*args, **kwargs):
        return SimpleNamespace(
            hits=[
                SimpleNamespace(
                    doc_id=doc_map[ac.name].id,
                    chunk_id=ac_chunk.id,
                    score=0.87,
                    title_en=doc_map[ac.name].title_en,
                    title_zh=doc_map[ac.name].title_zh,
                    category_path=doc_map[ac.name].category_path,
                ),
                SimpleNamespace(
                    doc_id=doc_map[prop.name].id,
                    chunk_id=prop_chunk.id,
                    score=0.81,
                    title_en=doc_map[prop.name].title_en,
                    title_zh=doc_map[prop.name].title_zh,
                    category_path=doc_map[prop.name].category_path,
                ),
            ],
            query_en="air conditioner warranty and property fee",
            bilingual=True,
            qdrant_used=True,
            retrieval_mode="hybrid",
            vector_hit_count=3,
            lexical_hit_count=2,
        )

    monkeypatch.setattr(agent_service, "search_documents", _fake_search)
    planner = {
        "intent": "search_documents",
        "confidence": 0.91,
        "doc_scope": {},
        "actions": ["search_documents"],
        "fallback": "fallback_search",
        "ui_lang": "zh",
        "query_lang": "zh",
    }
    r = client.post(
        "/v1/agent/execute",
        json={"query": "房子空调和物业资料要点", "ui_lang": "zh", "query_lang": "zh", "planner": planner},
    )
    assert r.status_code == 200
    out = r.json()
    stats = out.get("executor_stats") or {}
    assert stats.get("route") == "search_bundle"
    assert stats.get("qdrant_used") is True
    assert stats.get("retrieval_mode") == "hybrid"
    names = {str(item.get("file_name") or "") for item in (out.get("related_docs") or [])}
    assert ac.name in names
    assert prop.name in names


def test_agent_network_bill_strict_filter(client, tmp_path: Path, monkeypatch):
    internet = tmp_path / "network_bill_focus.txt"
    power = tmp_path / "electric_bill_noise.txt"
    gas = tmp_path / "gas_bill_noise.txt"
    internet.write_text("Superloop internet nbn bill period 2026-02 and amount due.", encoding="utf-8")
    power.write_text("Electricity invoice and kwh usage details.", encoding="utf-8")
    gas.write_text("Gas invoice and usage details.", encoding="utf-8")
    client.post("/v1/ingestion/jobs", json={"file_paths": [str(internet), str(power), str(gas)]})

    with SessionLocal() as db:
        docs = db.query(Document).filter(Document.file_name.in_([internet.name, power.name, gas.name])).all()
        assert len(docs) == 3
        doc_map = {doc.file_name: doc for doc in docs}
        doc_map[internet.name].status = "completed"
        doc_map[internet.name].category_path = "finance/bills/internet"
        doc_map[internet.name].title_zh = "2026年2月互联网账单"
        doc_map[power.name].status = "completed"
        doc_map[power.name].category_path = "finance/bills/electricity"
        doc_map[power.name].title_zh = "2026年2月电费账单"
        doc_map[gas.name].status = "completed"
        doc_map[gas.name].category_path = "finance/bills/gas"
        doc_map[gas.name].title_zh = "2026年2月燃气账单"
        db.commit()

        internet_chunk = db.query(Chunk).filter(Chunk.document_id == doc_map[internet.name].id).order_by(Chunk.chunk_index.asc()).first()
        power_chunk = db.query(Chunk).filter(Chunk.document_id == doc_map[power.name].id).order_by(Chunk.chunk_index.asc()).first()
        gas_chunk = db.query(Chunk).filter(Chunk.document_id == doc_map[gas.name].id).order_by(Chunk.chunk_index.asc()).first()
        assert internet_chunk is not None
        assert power_chunk is not None
        assert gas_chunk is not None

    def _fake_search(*args, **kwargs):
        return SimpleNamespace(
            hits=[
                SimpleNamespace(
                    doc_id=doc_map[power.name].id,
                    chunk_id=power_chunk.id,
                    score=0.92,
                    title_en=doc_map[power.name].title_en,
                    title_zh=doc_map[power.name].title_zh,
                    category_path=doc_map[power.name].category_path,
                ),
                SimpleNamespace(
                    doc_id=doc_map[internet.name].id,
                    chunk_id=internet_chunk.id,
                    score=0.81,
                    title_en=doc_map[internet.name].title_en,
                    title_zh=doc_map[internet.name].title_zh,
                    category_path=doc_map[internet.name].category_path,
                ),
                SimpleNamespace(
                    doc_id=doc_map[gas.name].id,
                    chunk_id=gas_chunk.id,
                    score=0.78,
                    title_en=doc_map[gas.name].title_en,
                    title_zh=doc_map[gas.name].title_zh,
                    category_path=doc_map[gas.name].category_path,
                ),
            ],
            query_en="home internet bill",
            bilingual=True,
            qdrant_used=True,
            retrieval_mode="hybrid",
            vector_hit_count=7,
            lexical_hit_count=1,
        )

    monkeypatch.setattr(agent_service, "search_documents", _fake_search)
    planner = {
        "intent": "search_documents",
        "confidence": 0.88,
        "doc_scope": {},
        "actions": ["search_documents"],
        "fallback": "fallback_search",
        "ui_lang": "zh",
        "query_lang": "zh",
    }
    r = client.post(
        "/v1/agent/execute",
        json={"query": "家里的网络账单", "ui_lang": "zh", "query_lang": "zh", "planner": planner},
    )
    assert r.status_code == 200
    out = r.json()
    related = out.get("related_docs") or []
    assert len(related) >= 1
    assert all(str(item.get("category_path") or "") == "finance/bills/internet" for item in related)
    stats = out.get("executor_stats") or {}
    assert stats.get("facet_mode") == "strict_topic"
    assert "network_bill" in (stats.get("facet_keys") or [])


def test_agent_property_contact_strict_filter(client, tmp_path: Path, monkeypatch):
    property_doc = tmp_path / "property_contact_guide.txt"
    power_doc = tmp_path / "electric_bill_contact_noise.txt"
    property_doc.write_text(
        "Wonder Property manager contact phone 0403000000 and email manager@example.com.",
        encoding="utf-8",
    )
    power_doc.write_text("Electricity bill support contact phone 1300 000 000.", encoding="utf-8")
    client.post("/v1/ingestion/jobs", json={"file_paths": [str(property_doc), str(power_doc)]})

    with SessionLocal() as db:
        docs = db.query(Document).filter(Document.file_name.in_([property_doc.name, power_doc.name])).all()
        assert len(docs) == 2
        doc_map = {doc.file_name: doc for doc in docs}
        doc_map[property_doc.name].status = "completed"
        doc_map[property_doc.name].category_path = "home/property"
        doc_map[property_doc.name].title_zh = "物业联系方式"
        doc_map[power_doc.name].status = "completed"
        doc_map[power_doc.name].category_path = "finance/bills/electricity"
        doc_map[power_doc.name].title_zh = "2026年2月电费账单"
        db.commit()

        property_chunk = (
            db.query(Chunk).filter(Chunk.document_id == doc_map[property_doc.name].id).order_by(Chunk.chunk_index.asc()).first()
        )
        power_chunk = db.query(Chunk).filter(Chunk.document_id == doc_map[power_doc.name].id).order_by(Chunk.chunk_index.asc()).first()
        assert property_chunk is not None
        assert power_chunk is not None

    def _fake_search(*args, **kwargs):
        return SimpleNamespace(
            hits=[
                SimpleNamespace(
                    doc_id=doc_map[power_doc.name].id,
                    chunk_id=power_chunk.id,
                    score=0.91,
                    title_en=doc_map[power_doc.name].title_en,
                    title_zh=doc_map[power_doc.name].title_zh,
                    category_path=doc_map[power_doc.name].category_path,
                ),
                SimpleNamespace(
                    doc_id=doc_map[property_doc.name].id,
                    chunk_id=property_chunk.id,
                    score=0.84,
                    title_en=doc_map[property_doc.name].title_en,
                    title_zh=doc_map[property_doc.name].title_zh,
                    category_path=doc_map[property_doc.name].category_path,
                ),
            ],
            query_en="property contact info",
            bilingual=True,
            qdrant_used=True,
            retrieval_mode="hybrid",
            vector_hit_count=5,
            lexical_hit_count=2,
        )

    monkeypatch.setattr(agent_service, "search_documents", _fake_search)
    planner = {
        "intent": "search_documents",
        "confidence": 0.86,
        "doc_scope": {},
        "actions": ["search_documents"],
        "fallback": "fallback_search",
        "ui_lang": "zh",
        "query_lang": "zh",
    }
    r = client.post(
        "/v1/agent/execute",
        json={"query": "物业的联系方式是什么", "ui_lang": "zh", "query_lang": "zh", "planner": planner},
    )
    assert r.status_code == 200
    out = r.json()
    related = out.get("related_docs") or []
    allowed = {"home/maintenance", "home/property", "legal/property", "finance/bills/other"}
    assert len(related) >= 1
    assert all(str(item.get("category_path") or "") in allowed for item in related)
    blocked = {"finance/bills/electricity", "finance/bills/water", "finance/bills/gas", "finance/bills/internet"}
    assert all(str(item.get("category_path") or "") not in blocked for item in related)
    stats = out.get("executor_stats") or {}
    assert stats.get("facet_mode") == "strict_topic"
    assert "property_contact" in (stats.get("facet_keys") or [])


def test_agent_strict_filter_zero_hit_returns_empty_related_docs(client, tmp_path: Path, monkeypatch):
    power = tmp_path / "electric_bill_only.txt"
    power.write_text("Electricity invoice and meter usage.", encoding="utf-8")
    client.post("/v1/ingestion/jobs", json={"file_paths": [str(power)]})

    with SessionLocal() as db:
        doc = db.query(Document).filter(Document.file_name == power.name).first()
        assert doc is not None
        doc.status = "completed"
        doc.category_path = "finance/bills/electricity"
        db.commit()
        chunk = db.query(Chunk).filter(Chunk.document_id == doc.id).order_by(Chunk.chunk_index.asc()).first()
        assert chunk is not None

    def _fake_search(*args, **kwargs):
        return SimpleNamespace(
            hits=[
                SimpleNamespace(
                    doc_id=doc.id,
                    chunk_id=chunk.id,
                    score=0.92,
                    title_en=doc.title_en,
                    title_zh=doc.title_zh,
                    category_path=doc.category_path,
                )
            ],
            query_en="home internet bill",
            bilingual=False,
            qdrant_used=True,
            retrieval_mode="hybrid",
            vector_hit_count=3,
            lexical_hit_count=0,
        )

    monkeypatch.setattr(agent_service, "search_documents", _fake_search)
    monkeypatch.setattr(agent_service.requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(agent_service.requests.exceptions.Timeout()))
    planner = {
        "intent": "search_documents",
        "confidence": 0.86,
        "doc_scope": {},
        "actions": ["search_documents"],
        "fallback": "fallback_search",
        "ui_lang": "zh",
        "query_lang": "zh",
    }
    r = client.post(
        "/v1/agent/execute",
        json={"query": "家里的网络账单", "ui_lang": "zh", "query_lang": "zh", "planner": planner},
    )
    assert r.status_code == 200
    out = r.json()
    assert (out.get("related_docs") or []) == []
    stats = out.get("executor_stats") or {}
    assert stats.get("fallback_reason") == "strict_filter_zero_hit"
    assert stats.get("facet_mode") == "strict_topic"


def test_agent_context_policy_fresh_turn_ignores_history(client, monkeypatch):
    captured = {"conversation": None}

    monkeypatch.setattr(
        agent_service,
        "_execute_plan",
        lambda *args, **kwargs: {
            "route": "search_bundle",
            "context_chunks": [
                {
                    "doc_id": "doc-1",
                    "chunk_id": "c-1",
                    "title_en": "internet bill",
                    "title_zh": "网络账单",
                    "category_path": "finance/bills/internet",
                    "score": 0.9,
                    "text": "contact phone 1800578737 email billing@home.superloop.com",
                }
            ],
            "sources": [{"doc_id": "doc-1", "chunk_id": "c-1", "label": "网络账单"}],
            "related_docs": [],
            "hit_count": 1,
            "doc_count": 1,
            "bilingual_search": False,
            "qdrant_used": False,
            "retrieval_mode": "none",
            "vector_hit_count": 0,
            "lexical_hit_count": 0,
            "fallback_reason": "",
            "facet_mode": "none",
            "facet_keys": [],
            "fact_route": "none",
            "fact_month": "",
        },
    )

    def _fake_synth(req, planner, bundle, *, trace_id, conversation):
        captured["conversation"] = list(conversation)
        return (None, "forced")

    monkeypatch.setattr(agent_service, "_synthesize_with_model", _fake_synth)

    r = client.post(
        "/v1/agent/execute",
        json={
            "query": "NBN的联系方式是什么",
            "ui_lang": "zh",
            "query_lang": "zh",
            "planner": {
                "intent": "entity_fact_lookup",
                "confidence": 0.9,
                "doc_scope": {},
                "actions": ["search_documents"],
                "fallback": "search_semantic",
                "ui_lang": "zh",
                "query_lang": "zh",
                "required_evidence_fields": [],
                "refusal_candidate": False,
                "route_reason": "unit_test",
            },
            "conversation": [
                {"role": "user", "content": "上一轮问题"},
                {"role": "assistant", "content": "上一轮回答"},
            ],
        },
    )
    assert r.status_code == 200
    out = r.json()
    assert out.get("executor_stats", {}).get("context_policy") == "fresh_turn"
    assert captured["conversation"] == []


def test_agent_context_policy_followup_turn_keeps_history(client, monkeypatch):
    captured = {"conversation": None}

    monkeypatch.setattr(
        agent_service,
        "_execute_plan",
        lambda *args, **kwargs: {
            "route": "search_bundle",
            "context_chunks": [
                {
                    "doc_id": "doc-1",
                    "chunk_id": "c-1",
                    "title_en": "internet bill",
                    "title_zh": "网络账单",
                    "category_path": "finance/bills/internet",
                    "score": 0.9,
                    "text": "contact phone 1800578737 email billing@home.superloop.com",
                }
            ],
            "sources": [{"doc_id": "doc-1", "chunk_id": "c-1", "label": "网络账单"}],
            "related_docs": [],
            "hit_count": 1,
            "doc_count": 1,
            "bilingual_search": False,
            "qdrant_used": False,
            "retrieval_mode": "none",
            "vector_hit_count": 0,
            "lexical_hit_count": 0,
            "fallback_reason": "",
            "facet_mode": "none",
            "facet_keys": [],
            "fact_route": "none",
            "fact_month": "",
        },
    )

    def _fake_synth(req, planner, bundle, *, trace_id, conversation):
        captured["conversation"] = list(conversation)
        return (None, "forced")

    monkeypatch.setattr(agent_service, "_synthesize_with_model", _fake_synth)

    r = client.post(
        "/v1/agent/execute",
        json={
            "query": "继续看它的联系方式",
            "ui_lang": "zh",
            "query_lang": "zh",
            "planner": {
                "intent": "entity_fact_lookup",
                "confidence": 0.9,
                "doc_scope": {},
                "actions": ["search_documents"],
                "fallback": "search_semantic",
                "ui_lang": "zh",
                "query_lang": "zh",
                "required_evidence_fields": [],
                "refusal_candidate": False,
                "route_reason": "unit_test",
            },
            "conversation": [
                {"role": "user", "content": "先查网络账单"},
                {"role": "assistant", "content": "找到互联网账单"},
            ],
        },
    )
    assert r.status_code == 200
    out = r.json()
    assert out.get("executor_stats", {}).get("context_policy") == "followup_turn"
    assert len(captured["conversation"] or []) >= 1


def test_agent_monthly_bill_total_route_uses_structured_facts(client, tmp_path: Path, monkeypatch):
    internet = tmp_path / "internet_bill.txt"
    power = tmp_path / "power_bill.txt"
    internet.write_text("internet bill", encoding="utf-8")
    power.write_text("power bill", encoding="utf-8")

    client.post("/v1/ingestion/jobs", json={"file_paths": [str(internet), str(power)]})

    with SessionLocal() as db:
        docs = db.query(Document).filter(Document.file_name.in_([internet.name, power.name])).all()
        for doc in docs:
            doc.status = "completed"
            doc.category_path = "finance/bills/internet" if doc.file_name == internet.name else "finance/bills/electricity"
            doc.category_label_zh = "账单与缴费"
            doc.category_label_en = "Bills"
        db.commit()
        docs_map = {doc.file_name: doc for doc in docs}

    fake_rows = [
        (
            SimpleNamespace(
                amount_due=109.0,
                currency="AUD",
                due_date=dt.datetime(2026, 3, 10, tzinfo=dt.UTC),
                billing_period_start=dt.datetime(2026, 2, 8, tzinfo=dt.UTC),
                billing_period_end=dt.datetime(2026, 3, 7, tzinfo=dt.UTC),
                payment_status="unpaid",
                confidence=0.92,
            ),
            SimpleNamespace(
                id=docs_map[internet.name].id,
                file_name=docs_map[internet.name].file_name,
                source_path=docs_map[internet.name].source_path,
                title_en=docs_map[internet.name].title_en,
                title_zh="2026年2月互联网账单",
                category_path=docs_map[internet.name].category_path,
            ),
        ),
        (
            SimpleNamespace(
                amount_due=51.09,
                currency="AUD",
                due_date=dt.datetime(2026, 2, 19, tzinfo=dt.UTC),
                billing_period_start=dt.datetime(2026, 1, 5, tzinfo=dt.UTC),
                billing_period_end=dt.datetime(2026, 2, 2, tzinfo=dt.UTC),
                payment_status="paid",
                confidence=0.91,
            ),
            SimpleNamespace(
                id=docs_map[power.name].id,
                file_name=docs_map[power.name].file_name,
                source_path=docs_map[power.name].source_path,
                title_en=docs_map[power.name].title_en,
                title_zh="2026年2月电费账单",
                category_path=docs_map[power.name].category_path,
            ),
        ),
    ]
    monkeypatch.setattr(agent_service, "list_recent_bill_facts", lambda *args, **kwargs: fake_rows)

    r = client.post("/v1/agent/execute", json={"query": "2月的账单情况，一共多少钱", "ui_lang": "zh", "query_lang": "zh"})
    assert r.status_code == 200
    out = r.json()
    stats = out.get("executor_stats") or {}
    assert stats.get("route") == "bill_monthly_total"
    assert stats.get("fact_route") == "bill_monthly_total"
    assert str(stats.get("fact_month") or "") == "2026-02"
    related = out.get("related_docs") or []
    assert related
    assert all(str(item.get("category_path") or "").startswith("finance/bills/") for item in related)
    assert int(stats.get("doc_count") or 0) >= 2


def test_agent_english_current_bills_uses_bill_attention_structured_route(client, monkeypatch):
    fake_rows = [
        (
            SimpleNamespace(amount_due=109.0, currency="AUD", due_date=dt.datetime(2026, 2, 23), payment_status="unpaid", confidence=0.9),
            SimpleNamespace(
                id="doc-1",
                file_name="internet_bill.pdf",
                source_path="/tmp/fake-1.pdf",
                title_en="2026-02 Internet Bill",
                title_zh="2026年2月互联网账单",
                category_path="finance/bills/internet",
            ),
        ),
        (
            SimpleNamespace(amount_due=51.09, currency="AUD", due_date=dt.datetime(2026, 2, 19), payment_status="paid", confidence=0.88),
            SimpleNamespace(
                id="doc-2",
                file_name="power_bill.pdf",
                source_path="/tmp/fake-2.pdf",
                title_en="2026-02 Electricity Bill",
                title_zh="2026年2月电费账单",
                category_path="finance/bills/electricity",
            ),
        ),
    ]
    monkeypatch.setattr(agent_service, "list_recent_bill_facts", lambda *args, **kwargs: fake_rows)
    monkeypatch.setattr(agent_service.crud, "source_path_available", lambda *args, **kwargs: True)
    monkeypatch.setattr(agent_service, "_build_related_docs", lambda *args, **kwargs: [])

    r = client.post(
        "/v1/agent/execute",
        json={
            "query": "current bills",
            "ui_lang": "en",
            "query_lang": "en",
            "planner": {
                "intent": "list_recent",
                "confidence": 0.91,
                "doc_scope": {},
                "actions": ["list_recent"],
                "fallback": "search_semantic",
                "ui_lang": "en",
                "query_lang": "en",
                "route_reason": "unit_test",
            },
        },
    )
    assert r.status_code == 200
    out = r.json()
    stats = out.get("executor_stats") or {}
    assert stats.get("route") == "bill_attention"
    assert stats.get("retrieval_mode") == "structured"


def test_agent_monthly_bill_total_zero_hit_message_is_not_zero_template(client, monkeypatch):
    monkeypatch.setattr(agent_service, "list_recent_bill_facts", lambda *args, **kwargs: [])
    r = client.post("/v1/agent/execute", json={"query": "2024年2月份账单情况", "ui_lang": "zh", "query_lang": "zh"})
    assert r.status_code == 200
    out = r.json()
    stats = out.get("executor_stats") or {}
    assert stats.get("route") == "bill_monthly_total"
    assert stats.get("fallback_reason") == "bill_monthly_empty"
    summary = str((out.get("card") or {}).get("short_summary", {}).get("zh") or "")
    assert "未找到" in summary
    assert "澳币0.00" not in summary


def test_agent_english_birthday_query_refuses_without_birthdate_evidence(client, monkeypatch):
    monkeypatch.setattr(
        agent_service,
        "_execute_plan",
        lambda *args, **kwargs: {
            "route": "entity_fact_lookup",
            "detail_topic": "pets",
            "context_chunks": [
                {
                    "doc_id": "doc-pet-1",
                    "chunk_id": "c-1",
                    "title_en": "Sterilisation Certificate for Fluffy",
                    "title_zh": "Fluffy绝育证书",
                    "category_path": "health/medical_records",
                    "score": 0.92,
                    "text": "Fluffy sterilisation certificate dated 2025-10-31 at City Vet Hospital.",
                }
            ],
            "sources": [{"doc_id": "doc-pet-1", "chunk_id": "c-1", "label": "Sterilisation Certificate for Fluffy"}],
            "related_docs": [],
            "hit_count": 1,
            "doc_count": 1,
            "bilingual_search": False,
            "qdrant_used": False,
            "retrieval_mode": "structured",
            "vector_hit_count": 0,
            "lexical_hit_count": 0,
            "fallback_reason": "",
            "detail_mode": "structured",
            "detail_rows_count": 0,
            "detail_sections": [],
            "missing_fields": [],
            "coverage_stats": {"docs_scanned": 1, "docs_matched": 1, "fields_filled": 0},
            "fact_route": "none",
            "fact_month": "",
            "route_reason": "unit_test",
        },
    )
    monkeypatch.setattr(
        agent_service,
        "_synthesize_with_model",
        lambda *args, **kwargs: (
            None,
            "should_not_be_used",
        ),
    )
    r = client.post(
        "/v1/agent/execute",
        json={
            "query": "Fluffy's birthday",
            "ui_lang": "en",
            "query_lang": "en",
            "planner": {
                "intent": "entity_fact_lookup",
                "confidence": 0.92,
                "doc_scope": {},
                "actions": ["search_documents", "extract_fields"],
                "fallback": "search_semantic",
                "ui_lang": "en",
                "query_lang": "en",
                "required_evidence_fields": ["date"],
                "refusal_candidate": False,
                "route_reason": "unit_test",
            },
        },
    )
    assert r.status_code == 200
    out = r.json()
    stats = out.get("executor_stats") or {}
    assert stats.get("route") == "entity_fact_lookup"
    assert stats.get("synth_error_code") == "insufficient_evidence"
    assert stats.get("answer_mode") == "refusal"
    summary_en = str((out.get("card") or {}).get("short_summary", {}).get("en") or "")
    assert "Not enough evidence" in summary_en
    assert "sterilis" not in summary_en.lower()


def test_agent_related_docs_order_follows_score_not_updated_time(client, tmp_path: Path, monkeypatch):
    high = tmp_path / "manual_high_score.txt"
    low = tmp_path / "manual_low_score.txt"
    high.write_text("aircon compressor warranty details and service period", encoding="utf-8")
    low.write_text("aircon quick start guide", encoding="utf-8")
    client.post("/v1/ingestion/jobs", json={"file_paths": [str(high), str(low)]})

    with SessionLocal() as db:
        docs = db.query(Document).filter(Document.file_name.in_([high.name, low.name])).all()
        assert len(docs) == 2
        doc_map = {doc.file_name: doc for doc in docs}
        now = dt.datetime.now(dt.UTC)
        doc_map[high.name].status = "completed"
        doc_map[high.name].category_path = "home/manuals"
        doc_map[high.name].updated_at = now - dt.timedelta(days=2)
        doc_map[low.name].status = "completed"
        doc_map[low.name].category_path = "home/manuals"
        doc_map[low.name].updated_at = now
        db.commit()

        high_chunk = db.query(Chunk).filter(Chunk.document_id == doc_map[high.name].id).order_by(Chunk.chunk_index.asc()).first()
        low_chunk = db.query(Chunk).filter(Chunk.document_id == doc_map[low.name].id).order_by(Chunk.chunk_index.asc()).first()
        assert high_chunk is not None
        assert low_chunk is not None

    def _fake_search(*args, **kwargs):
        return SimpleNamespace(
            hits=[
                SimpleNamespace(
                    doc_id=doc_map[high.name].id,
                    chunk_id=high_chunk.id,
                    score=0.95,
                    title_en=doc_map[high.name].title_en,
                    title_zh=doc_map[high.name].title_zh,
                    category_path=doc_map[high.name].category_path,
                ),
                SimpleNamespace(
                    doc_id=doc_map[low.name].id,
                    chunk_id=low_chunk.id,
                    score=0.44,
                    title_en=doc_map[low.name].title_en,
                    title_zh=doc_map[low.name].title_zh,
                    category_path=doc_map[low.name].category_path,
                ),
            ],
            query_en="aircon warranty",
            bilingual=False,
            qdrant_used=True,
            retrieval_mode="hybrid",
            vector_hit_count=2,
            lexical_hit_count=1,
        )

    monkeypatch.setattr(agent_service, "search_documents", _fake_search)
    monkeypatch.setattr(agent_service.requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(agent_service.requests.exceptions.Timeout()))
    planner = {
        "intent": "search_documents",
        "confidence": 0.9,
        "doc_scope": {},
        "actions": ["search_documents"],
        "fallback": "fallback_search",
        "ui_lang": "zh",
        "query_lang": "zh",
    }
    r = client.post(
        "/v1/agent/execute",
        json={"query": "空调保修条款", "ui_lang": "zh", "query_lang": "zh", "planner": planner},
    )
    assert r.status_code == 200
    related = r.json().get("related_docs") or []
    assert len(related) >= 2
    assert str(related[0].get("doc_id") or "") == doc_map[high.name].id
    assert str(related[1].get("doc_id") or "") == doc_map[low.name].id


def test_agent_synth_fallback_stats_and_text_are_user_facing(client, tmp_path: Path, monkeypatch):
    sample = tmp_path / "property_contact_sample.txt"
    sample.write_text("Property manager contact phone and email details.", encoding="utf-8")
    client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})

    with SessionLocal() as db:
        doc = db.query(Document).filter(Document.file_name == sample.name).first()
        assert doc is not None
        doc.status = "completed"
        doc.category_path = "home/property"
        db.commit()
        chunk = db.query(Chunk).filter(Chunk.document_id == doc.id).order_by(Chunk.chunk_index.asc()).first()
        assert chunk is not None

    def _fake_search(*args, **kwargs):
        return SimpleNamespace(
            hits=[
                SimpleNamespace(
                    doc_id=doc.id,
                    chunk_id=chunk.id,
                    score=0.88,
                    title_en=doc.title_en,
                    title_zh=doc.title_zh,
                    category_path=doc.category_path,
                )
            ],
            query_en="property contact",
            bilingual=False,
            qdrant_used=True,
            retrieval_mode="hybrid",
            vector_hit_count=2,
            lexical_hit_count=1,
        )

    monkeypatch.setattr(agent_service, "search_documents", _fake_search)
    monkeypatch.setattr(agent_service.requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(agent_service.requests.exceptions.Timeout()))
    planner = {
        "intent": "search_documents",
        "confidence": 0.85,
        "doc_scope": {},
        "actions": ["search_documents"],
        "fallback": "fallback_search",
        "ui_lang": "zh",
        "query_lang": "zh",
    }
    r = client.post(
        "/v1/agent/execute",
        json={"query": "物业的联系方式是什么", "ui_lang": "zh", "query_lang": "zh", "planner": planner},
    )
    assert r.status_code == 200
    out = r.json()
    stats = out.get("executor_stats") or {}
    assert stats.get("synth_fallback_used") is True
    assert stats.get("synth_error_code") in {"synth_timeout", "insufficient_evidence"}
    summary_zh = str((out.get("card") or {}).get("short_summary", {}).get("zh") or "")
    assert "意图" not in summary_zh
    assert "回退路由" not in summary_zh
    assert "命中分块" not in summary_zh
