from pathlib import Path

from pypdf import PdfWriter

from app.db import SessionLocal
from app.models import Chunk, Document, DocumentStatus


def _insert_document(path: Path, *, status: str = DocumentStatus.COMPLETED.value, file_ext: str | None = None) -> str:
    db = SessionLocal()
    try:
        ext = str(file_ext or path.suffix.lstrip(".") or "bin").strip().lower()
        size = int(path.stat().st_size) if path.exists() else 0
        doc = Document(
            source_path=str(path),
            file_name=path.name,
            file_ext=ext,
            file_size=size,
            sha256="a" * 64,
            status=status,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return str(doc.id)
    finally:
        db.close()


def _insert_chunk(doc_id: str, text: str) -> None:
    db = SessionLocal()
    try:
        row = Chunk(
            document_id=str(doc_id),
            chunk_index=0,
            content=str(text or ""),
            token_count=max(1, len(str(text or "").split())),
            embedding_status="ready",
        )
        db.add(row)
        db.commit()
    finally:
        db.close()


def _write_minimal_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as f:
        writer.write(f)


def test_document_content_inline_pdf_success(client, tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    _write_minimal_pdf(pdf_path)
    doc_id = _insert_document(pdf_path)

    r = client.get(f"/v1/documents/{doc_id}/content")
    assert r.status_code == 200
    assert str(r.headers.get("content-type") or "").startswith("application/pdf")
    assert "inline" in str(r.headers.get("content-disposition") or "").lower()
    assert str(r.headers.get("x-content-type-options") or "").lower() == "nosniff"
    assert "private" in str(r.headers.get("cache-control") or "").lower()


def test_document_content_attachment_header(client, tmp_path: Path):
    txt_path = tmp_path / "sample.txt"
    txt_path.write_text("hello world", encoding="utf-8")
    doc_id = _insert_document(txt_path)

    r = client.get(f"/v1/documents/{doc_id}/content?disposition=attachment")
    assert r.status_code == 200
    assert "attachment" in str(r.headers.get("content-disposition") or "").lower()
    assert "sample.txt" in str(r.headers.get("content-disposition") or "")


def test_document_content_missing_source_file_returns_404(client, tmp_path: Path):
    missing_path = tmp_path / "missing.pdf"
    doc_id = _insert_document(missing_path)

    r = client.get(f"/v1/documents/{doc_id}/content")
    assert r.status_code == 404
    assert r.json().get("detail") == "source_file_missing"


def test_document_content_not_ready_returns_409(client, tmp_path: Path):
    txt_path = tmp_path / "pending.txt"
    txt_path.write_text("processing", encoding="utf-8")
    doc_id = _insert_document(txt_path, status=DocumentStatus.FAILED.value)

    r = client.get(f"/v1/documents/{doc_id}/content")
    assert r.status_code == 409
    assert r.json().get("detail") == "document_not_ready"


def test_document_content_inline_unknown_type_returns_415(client, tmp_path: Path):
    blob = tmp_path / "payload.zzz"
    blob.write_bytes(b"\x00\x01\x02")
    doc_id = _insert_document(blob, file_ext="zzz")

    r = client.get(f"/v1/documents/{doc_id}/content")
    assert r.status_code == 415
    assert r.json().get("detail") == "unsupported_media_type"


def test_documents_default_hides_missing_source(client, tmp_path: Path):
    ok_pdf = tmp_path / "ok.pdf"
    _write_minimal_pdf(ok_pdf)
    _insert_document(ok_pdf)

    missing_pdf = tmp_path / "missing.pdf"
    _insert_document(missing_pdf)

    r = client.get("/v1/documents?status=completed&limit=50&offset=0")
    assert r.status_code == 200
    out = r.json()
    names = {str(item.get("file_name") or "") for item in out.get("items") or []}
    assert "ok.pdf" in names
    assert "missing.pdf" not in names


def test_documents_include_missing_returns_missing_source(client, tmp_path: Path):
    ok_pdf = tmp_path / "ok2.pdf"
    _write_minimal_pdf(ok_pdf)
    _insert_document(ok_pdf)
    missing_pdf = tmp_path / "missing2.pdf"
    _insert_document(missing_pdf)

    r = client.get("/v1/documents?status=completed&include_missing=true&limit=50&offset=0")
    assert r.status_code == 200
    out = r.json()
    by_name = {str(item.get("file_name") or ""): item for item in out.get("items") or []}
    assert "ok2.pdf" in by_name
    assert "missing2.pdf" in by_name
    assert by_name["ok2.pdf"]["source_available"] is True
    assert by_name["missing2.pdf"]["source_available"] is False
    assert by_name["missing2.pdf"]["source_missing_reason"] == "source_file_missing"


def test_document_detail_contains_source_available_fields(client, tmp_path: Path):
    ok_pdf = tmp_path / "detail_ok.pdf"
    _write_minimal_pdf(ok_pdf)
    ok_id = _insert_document(ok_pdf)
    missing_pdf = tmp_path / "detail_missing.pdf"
    missing_id = _insert_document(missing_pdf)

    ok = client.get(f"/v1/documents/{ok_id}")
    assert ok.status_code == 200
    ok_doc = ok.json()
    assert ok_doc["source_available"] is True
    assert ok_doc["source_missing_reason"] == ""

    missing = client.get(f"/v1/documents/{missing_id}")
    assert missing.status_code == 200
    missing_doc = missing.json()
    assert missing_doc["source_available"] is False
    assert missing_doc["source_missing_reason"] == "source_file_missing"


def test_content_availability_endpoint_states(client, tmp_path: Path):
    ok_pdf = tmp_path / "availability_ok.pdf"
    _write_minimal_pdf(ok_pdf)
    ok_id = _insert_document(ok_pdf)

    missing_pdf = tmp_path / "availability_missing.pdf"
    missing_id = _insert_document(missing_pdf)

    not_ready_txt = tmp_path / "availability_pending.txt"
    not_ready_txt.write_text("pending", encoding="utf-8")
    not_ready_id = _insert_document(not_ready_txt, status=DocumentStatus.FAILED.value)

    unknown = tmp_path / "availability_unknown.zzz"
    unknown.write_bytes(b"\x00\x01")
    unknown_id = _insert_document(unknown, file_ext="zzz")

    r_ok = client.get(f"/v1/documents/{ok_id}/content/availability")
    assert r_ok.status_code == 200
    assert r_ok.json() == {
        "doc_id": ok_id,
        "source_available": True,
        "inline_supported": True,
        "detail": "ok",
    }

    r_missing = client.get(f"/v1/documents/{missing_id}/content/availability")
    assert r_missing.status_code == 200
    assert r_missing.json()["source_available"] is False
    assert r_missing.json()["inline_supported"] is False
    assert r_missing.json()["detail"] == "source_file_missing"

    r_not_ready = client.get(f"/v1/documents/{not_ready_id}/content/availability")
    assert r_not_ready.status_code == 200
    assert r_not_ready.json()["source_available"] is False
    assert r_not_ready.json()["detail"] == "document_not_ready"

    r_unknown = client.get(f"/v1/documents/{unknown_id}/content/availability")
    assert r_unknown.status_code == 200
    assert r_unknown.json()["source_available"] is True
    assert r_unknown.json()["inline_supported"] is False
    assert r_unknown.json()["detail"] == "unsupported_media_type"


def test_search_excludes_missing_source_by_default(client, tmp_path: Path):
    ok_txt = tmp_path / "search_ok.txt"
    ok_txt.write_text("ok", encoding="utf-8")
    ok_id = _insert_document(ok_txt, file_ext="txt")
    _insert_chunk(ok_id, "warranty coverage and conditions")

    missing_txt = tmp_path / "search_missing.txt"
    missing_id = _insert_document(missing_txt, file_ext="txt")
    _insert_chunk(missing_id, "warranty coverage and conditions")

    r_default = client.post(
        "/v1/search",
        json={"query": "warranty", "top_k": 10, "score_threshold": 0.0, "ui_lang": "en", "query_lang": "en"},
    )
    assert r_default.status_code == 200
    ids_default = {str(hit.get("doc_id") or "") for hit in r_default.json().get("hits") or []}
    assert ok_id in ids_default
    assert missing_id not in ids_default

    r_all = client.post(
        "/v1/search",
        json={
            "query": "warranty",
            "top_k": 10,
            "score_threshold": 0.0,
            "ui_lang": "en",
            "query_lang": "en",
            "include_missing": True,
        },
    )
    assert r_all.status_code == 200
    ids_all = {str(hit.get("doc_id") or "") for hit in r_all.json().get("hits") or []}
    assert ok_id in ids_all
    assert missing_id in ids_all


def test_categories_exclude_missing_source_by_default(client, tmp_path: Path):
    ok_txt = tmp_path / "cat_ok.txt"
    ok_txt.write_text("cat", encoding="utf-8")
    _insert_document(ok_txt, file_ext="txt")

    missing_txt = tmp_path / "cat_missing.txt"
    _insert_document(missing_txt, file_ext="txt")

    r_default = client.get("/v1/categories")
    assert r_default.status_code == 200
    total_default = int(r_default.json().get("total_categories") or 0)
    assert total_default >= 1

    r_all = client.get("/v1/categories?include_missing=true")
    assert r_all.status_code == 200
    total_all = int(r_all.json().get("total_categories") or 0)
    assert total_all >= total_default
