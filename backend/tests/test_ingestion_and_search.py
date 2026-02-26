from pathlib import Path

from PIL import Image
from pypdf import PdfWriter

from app.services import ingestion, parsing


def test_ingestion_dedup_and_search(client, tmp_path: Path):
    sample = tmp_path / "electricity_bill.txt"
    sample.write_text("Electricity usage 2025 July peak month. 总电费 2140 澳元。", encoding="utf-8")

    r1 = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert r1.status_code == 200
    j1 = r1.json()
    assert j1["success_count"] == 1
    assert j1["failed_count"] == 0

    r2 = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2["duplicate_count"] >= 1

    rs = client.post("/v1/search", json={"query": "electricity", "top_k": 5, "query_lang": "en", "ui_lang": "en"})
    assert rs.status_code == 200
    data = rs.json()
    assert len(data["hits"]) >= 1
    assert data["hits"][0]["doc_id"]
    assert data["hits"][0]["chunk_id"]


def test_document_has_bilingual_fields(client, tmp_path: Path):
    sample = tmp_path / "manual.txt"
    sample.write_text("This is a maintenance manual for home appliance.", encoding="utf-8")

    r = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert r.status_code == 200

    rs = client.post("/v1/search", json={"query": "maintenance", "top_k": 1, "query_lang": "en", "ui_lang": "en"})
    assert rs.status_code == 200
    hit = rs.json()["hits"][0]

    rd = client.get(f"/v1/documents/{hit['doc_id']}?include_chunks=true")
    assert rd.status_code == 200
    doc = rd.json()

    assert "title_en" in doc
    assert "title_zh" in doc
    assert "summary_en" in doc
    assert "summary_zh" in doc


def test_document_summary_is_content_focused(client, tmp_path: Path):
    sample = tmp_path / "bill_detail.txt"
    sample.write_text(
        "Tax Invoice. Billing period 2026-01-10 to 2026-02-10. Amount due $218.45 before 2026-02-28. "
        "Electricity usage increased in peak period and service fee also increased.",
        encoding="utf-8",
    )

    r = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert r.status_code == 200
    assert r.json()["success_count"] == 1

    rows = client.get("/v1/documents?limit=20&offset=0&status=completed").json().get("items") or []
    item = next(it for it in rows if it["file_name"] == "bill_detail.txt")
    doc = client.get(f"/v1/documents/{item['doc_id']}").json()

    summary_zh = str(doc.get("summary_zh") or "")
    summary_en = str(doc.get("summary_en") or "")
    assert summary_zh
    assert summary_en
    assert "已完成文档入库" not in summary_zh
    assert "分块" not in summary_zh
    assert "Ingested " not in summary_en
    assert "核心分析" in summary_zh
    assert any(token in summary_zh for token in ["金额", "账单", "电费", "日期"])
    assert any(token in summary_en.lower() for token in ["content focus", "key details", "core points"])


def test_ingestion_accepts_directory_path(client, tmp_path: Path):
    folder = tmp_path / "vault_docs"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a.txt").write_text("Power bill statement for March.", encoding="utf-8")
    (folder / "b.md").write_text("Insurance renewal details.", encoding="utf-8")
    (folder / "ignore.bin").write_bytes(b"\x00\x01\x02")

    r = client.post("/v1/ingestion/jobs", json={"file_paths": [str(folder)]})
    assert r.status_code == 200
    out = r.json()
    assert out["success_count"] >= 2

    rs = client.post("/v1/search", json={"query": "insurance", "top_k": 5, "query_lang": "en", "ui_lang": "en"})
    assert rs.status_code == 200
    hits = rs.json().get("hits") or []
    assert len(hits) >= 1


def test_pdf_ocr_fallback_ingests_scanned_like_pdf(client, tmp_path: Path, monkeypatch):
    pdf = tmp_path / "scanned_invoice.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=240)
    with open(pdf, "wb") as f:
        writer.write(f)

    monkeypatch.setattr(parsing, "extract_ocr_text", lambda _path: "Scanned utility invoice amount due 180")
    r = client.post("/v1/ingestion/jobs", json={"file_paths": [str(pdf)]})
    assert r.status_code == 200
    out = r.json()
    assert out["success_count"] == 1
    assert out["failed_count"] == 0

    rs = client.post("/v1/search", json={"query": "utility invoice", "top_k": 5, "query_lang": "en", "ui_lang": "en"})
    assert rs.status_code == 200
    assert len(rs.json().get("hits") or []) >= 1


def test_pdf_without_text_and_without_ocr_fails(client, tmp_path: Path, monkeypatch):
    pdf = tmp_path / "no_text.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(pdf, "wb") as f:
        writer.write(f)

    monkeypatch.setattr(ingestion.settings, "ingestion_metadata_fallback_enabled", False)
    monkeypatch.setattr(parsing, "extract_ocr_text", lambda _path: "")
    r = client.post("/v1/ingestion/jobs", json={"file_paths": [str(pdf)]})
    assert r.status_code == 200
    out = r.json()
    assert out["success_count"] == 0
    assert out["failed_count"] == 1


def test_image_without_ocr_uses_metadata_fallback(client, tmp_path: Path, monkeypatch):
    img = tmp_path / "garage_photo.png"
    Image.new("RGB", (200, 140), (245, 245, 245)).save(img)

    monkeypatch.setattr(parsing, "extract_ocr_text", lambda _path: "")
    r = client.post("/v1/ingestion/jobs", json={"file_paths": [str(img)]})
    assert r.status_code == 200
    out = r.json()
    assert out["success_count"] == 1
    assert out["failed_count"] == 0

    rows = client.get("/v1/documents?limit=20&offset=0&status=completed").json().get("items") or []
    hit = next(item for item in rows if item["file_name"] == "garage_photo.png")
    rd = client.get(f"/v1/documents/{hit['doc_id']}?include_chunks=true")
    assert rd.status_code == 200
    doc = rd.json()
    assert doc["error_code"] == "parse_empty_fallback"
    assert len(doc.get("chunks") or []) >= 1


def test_image_phash_near_duplicate_detection(client, tmp_path: Path, monkeypatch):
    img_a = tmp_path / "img_a.png"
    img_b = tmp_path / "img_b.png"
    Image.new("RGB", (160, 120), (240, 240, 240)).save(img_a)
    Image.new("RGB", (160, 120), (240, 240, 241)).save(img_b)

    def _fake_phash(path: str, **_kwargs):
        name = Path(path).name
        if name == "img_a.png":
            return "ffffffffffffffff"
        if name == "img_b.png":
            return "fffffffffffffffe"
        return ""

    monkeypatch.setattr(ingestion, "compute_image_phash", _fake_phash)
    monkeypatch.setattr(parsing, "extract_ocr_text", lambda _path: "family photo")

    r1 = client.post("/v1/ingestion/jobs", json={"file_paths": [str(img_a)]})
    assert r1.status_code == 200
    assert r1.json()["success_count"] == 1

    r2 = client.post("/v1/ingestion/jobs", json={"file_paths": [str(img_b)]})
    assert r2.status_code == 200
    out2 = r2.json()
    assert out2["duplicate_count"] == 1
    assert out2["success_count"] == 0

    rows = client.get("/v1/documents?limit=20&offset=0").json().get("items") or []
    a = next(item for item in rows if item["file_name"] == "img_a.png" and item["status"] == "completed")
    b = next(item for item in rows if item["file_name"] == "img_b.png" and item["status"] == "duplicate")
    db = client.get(f"/v1/documents/{b['doc_id']}")
    assert db.status_code == 200
    assert db.json()["duplicate_of"] == a["doc_id"]


def test_oversized_photo_is_rejected(client, tmp_path: Path, monkeypatch):
    img = tmp_path / "oversized_photo.jpg"
    img.write_bytes(b"a" * (2 * 1024 * 1024))
    monkeypatch.setattr(ingestion.settings, "photo_max_size_mb", 1)

    r = client.post("/v1/ingestion/jobs", json={"file_paths": [str(img)]})
    assert r.status_code == 200
    out = r.json()
    assert out["success_count"] == 0
    assert out["failed_count"] == 1

    rows = client.get("/v1/documents?limit=50&offset=0").json().get("items") or []
    item = next(it for it in rows if it["file_name"] == "oversized_photo.jpg")
    rd = client.get(f"/v1/documents/{item['doc_id']}")
    assert rd.status_code == 200
    doc = rd.json()
    assert doc["status"] == "failed"
    assert doc["error_code"] == "photo_too_large"


def test_friendly_name_auto_generated_from_content(client, tmp_path: Path):
    sample = tmp_path / "water_invoice_raw_name.txt"
    sample.write_text(
        "Yarra Valley Water bill notice. Billing period May 2023. Amount due 120 AUD.",
        encoding="utf-8",
    )
    r = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert r.status_code == 200
    assert r.json()["success_count"] == 1

    rows = client.get("/v1/documents?limit=20&offset=0&status=completed").json().get("items") or []
    item = next(it for it in rows if it["file_name"] == "water_invoice_raw_name.txt")
    doc = client.get(f"/v1/documents/{item['doc_id']}").json()
    assert "账单" in str(doc.get("title_zh") or "")
    assert "2023年5月" in str(doc.get("title_zh") or "")
    assert doc.get("file_name") == "water_invoice_raw_name.txt"


def test_friendly_name_can_be_user_edited(client, tmp_path: Path):
    sample = tmp_path / "meeting_note.txt"
    sample.write_text("Annual General Meeting notice for June 2026.", encoding="utf-8")
    r = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert r.status_code == 200
    assert r.json()["success_count"] == 1

    rows = client.get("/v1/documents?limit=20&offset=0&status=completed").json().get("items") or []
    item = next(it for it in rows if it["file_name"] == "meeting_note.txt")
    doc_id = item["doc_id"]

    u = client.patch(
        f"/v1/documents/{doc_id}/friendly-name",
        json={"friendly_name_zh": "2026年AGM会议通知", "friendly_name_en": "2026 AGM Meeting Notice"},
    )
    assert u.status_code == 200
    out = u.json()
    assert out["friendly_name_zh"] == "2026年AGM会议通知"
    assert out["friendly_name_en"] == "2026 AGM Meeting Notice"

    rd = client.get(f"/v1/documents/{doc_id}")
    assert rd.status_code == 200
    doc = rd.json()
    assert doc["title_zh"] == "2026年AGM会议通知"
    assert doc["title_en"] == "2026 AGM Meeting Notice"
    assert doc["file_name"] == "meeting_note.txt"


def test_ingestion_category_is_model_classified_after_summary(client, tmp_path: Path, monkeypatch):
    sample = tmp_path / "model_classify.txt"
    sample.write_text("Power bill for February 2026. Amount due $88.", encoding="utf-8")

    call_order: list[str] = []

    def _fake_summary(**_kwargs):
        call_order.append("summary")
        return ("Electricity bill summary for February 2026.", "2026年2月电费账单，金额澳币$88。")

    def _fake_classify(**kwargs):
        call_order.append("classify")
        assert kwargs.get("summary_zh") == "2026年2月电费账单，金额澳币$88。"
        return ("Electricity Bills", "电费账单", "finance/bills/electricity")

    monkeypatch.setattr(ingestion, "build_document_summaries", _fake_summary)
    monkeypatch.setattr(ingestion, "classify_category_from_summary", _fake_classify)
    monkeypatch.setattr(
        ingestion,
        "regenerate_friendly_name_from_summary",
        lambda **_kwargs: ("2026-02 Electricity Bill", "2026年2月电费账单"),
    )

    r = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert r.status_code == 200
    assert r.json()["success_count"] == 1
    assert call_order == ["summary", "classify"]

    rows = client.get("/v1/documents?limit=20&offset=0&status=completed").json().get("items") or []
    item = next(it for it in rows if it["file_name"] == "model_classify.txt")
    doc = client.get(f"/v1/documents/{item['doc_id']}").json()
    assert doc["category_path"] == "finance/bills/electricity"
    assert doc["category_label_en"] == "Electricity Bills"
    assert doc["category_label_zh"] == "电费账单"


def test_reprocess_cleans_old_qdrant_points(client, tmp_path: Path, monkeypatch):
    sample = tmp_path / "reprocess_cleanup.txt"
    sample.write_text("first version " * 40, encoding="utf-8")
    r1 = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert r1.status_code == 200
    assert r1.json()["success_count"] == 1

    rows = client.get("/v1/documents?limit=20&offset=0&status=completed").json().get("items") or []
    item = next(it for it in rows if it["file_name"] == "reprocess_cleanup.txt")
    doc_id = item["doc_id"]
    before = client.get(f"/v1/documents/{doc_id}?include_chunks=true").json()
    old_chunk_ids = [str(ch["chunk_id"]) for ch in (before.get("chunks") or [])]
    assert old_chunk_ids

    sample.write_text(("second version with more content " * 90).strip(), encoding="utf-8")

    captured: dict[str, list[str]] = {"deleted_ids": []}
    monkeypatch.setattr(ingestion, "upsert_records", lambda *_args, **_kwargs: None)

    def _fake_delete(ids, wait=True):
        captured["deleted_ids"] = list(ids)
        return {"requested": len(ids), "deleted": len(ids)}

    monkeypatch.setattr(ingestion, "delete_records_by_point_ids", _fake_delete)
    rr = client.post(f"/v1/documents/{doc_id}/reprocess")
    assert rr.status_code == 200

    expected = [ingestion.stable_point_id(doc_id, chunk_id) for chunk_id in old_chunk_ids]
    assert sorted(captured["deleted_ids"]) == sorted(expected)


def test_reprocess_cleanup_failure_marks_pending_error(client, tmp_path: Path, monkeypatch):
    sample = tmp_path / "reprocess_cleanup_fail.txt"
    sample.write_text("first version " * 32, encoding="utf-8")
    r1 = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert r1.status_code == 200
    assert r1.json()["success_count"] == 1

    rows = client.get("/v1/documents?limit=20&offset=0&status=completed").json().get("items") or []
    item = next(it for it in rows if it["file_name"] == "reprocess_cleanup_fail.txt")
    doc_id = item["doc_id"]

    sample.write_text(("second version for cleanup fail " * 80).strip(), encoding="utf-8")
    monkeypatch.setattr(ingestion, "upsert_records", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        ingestion,
        "delete_records_by_point_ids",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("delete_failed")),
    )

    rr = client.post(f"/v1/documents/{doc_id}/reprocess")
    assert rr.status_code == 200
    doc = client.get(f"/v1/documents/{doc_id}").json()
    assert doc["status"] == "completed"
    assert "qdrant_cleanup_pending" in str(doc.get("error_code") or "")
