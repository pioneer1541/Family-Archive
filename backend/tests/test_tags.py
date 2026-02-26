from pathlib import Path

from app.services.tag_rules import infer_auto_tags, normalize_tag_key


def _find_doc_id_by_file_name(client, file_name: str) -> str:
    rows = client.get("/v1/documents?limit=200&offset=0").json().get("items") or []
    for row in rows:
        if row.get("file_name") == file_name:
            return str(row.get("doc_id") or "")
    return ""


def test_tag_normalization_and_synonyms():
    assert normalize_tag_key("Vendor:AGL Energy") == "vendor:agl"
    assert normalize_tag_key("device:TP Link AX6000") == "device:tp-link-ax6000"
    assert normalize_tag_key("status:important") == "status:important"
    assert normalize_tag_key("status:done") == ""
    assert normalize_tag_key("free_text_tag") == ""


def test_document_tags_patch_and_search_filters(client, tmp_path: Path):
    d1 = tmp_path / "bill_agl.txt"
    d2 = tmp_path / "bill_telstra.txt"
    d1.write_text("Electricity bill due this month. vendor AGL.", encoding="utf-8")
    d2.write_text("Internet bill due this month. vendor Telstra.", encoding="utf-8")

    r1 = client.post("/v1/ingestion/jobs", json={"file_paths": [str(d1)]})
    r2 = client.post("/v1/ingestion/jobs", json={"file_paths": [str(d2)]})
    assert r1.status_code == 200
    assert r2.status_code == 200

    doc1 = _find_doc_id_by_file_name(client, "bill_agl.txt")
    doc2 = _find_doc_id_by_file_name(client, "bill_telstra.txt")
    assert doc1
    assert doc2

    p1 = client.patch(
        f"/v1/documents/{doc1}/tags",
        json={"add": ["vendor:agl", "status:important"], "remove": []},
    )
    p2 = client.patch(
        f"/v1/documents/{doc2}/tags",
        json={"add": ["vendor:telstra"], "remove": []},
    )
    assert p1.status_code == 200
    assert p2.status_code == 200

    s1 = client.post(
        "/v1/search",
        json={
            "query": "bill",
            "top_k": 10,
            "query_lang": "en",
            "ui_lang": "en",
            "tags_all": ["vendor:agl", "status:important"],
        },
    )
    assert s1.status_code == 200
    hits1 = s1.json().get("hits") or []
    assert len(hits1) >= 1
    assert all("vendor:agl" in (hit.get("tags") or []) for hit in hits1)

    s2 = client.post(
        "/v1/search",
        json={
            "query": "bill",
            "top_k": 10,
            "query_lang": "en",
            "ui_lang": "en",
            "tags_any": ["vendor:telstra"],
        },
    )
    assert s2.status_code == 200
    hits2 = s2.json().get("hits") or []
    assert len(hits2) >= 1
    assert any(hit.get("doc_id") == doc2 for hit in hits2)


def test_document_tags_limit_guard(client, tmp_path: Path):
    sample = tmp_path / "tags_limit.txt"
    sample.write_text("Tag limit guard test", encoding="utf-8")
    r = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert r.status_code == 200

    doc_id = _find_doc_id_by_file_name(client, "tags_limit.txt")
    assert doc_id

    tags = [f"vendor:test-{idx}" for idx in range(13)]
    p = client.patch(f"/v1/documents/{doc_id}/tags", json={"add": tags, "remove": []})
    assert p.status_code == 400
    assert p.json().get("detail") == "too_many_tags"


def test_auto_tags_for_mail_vendor():
    tags = infer_auto_tags(
        file_name="invoice.pdf",
        source_path="/app/data/mail_attachments/2026/invoice.pdf",
        source_type="mail",
        summary_en="Electricity bill from AGL for February 2026",
        summary_zh="2026年2月电费账单，来自AGL",
        content_excerpt="Amount due AUD $80",
        category_path="finance/bills/electricity",
        mail_from="billing@agl.com.au",
        mail_subject="Your monthly bill",
    )
    assert "vendor:agl" in tags
