from pathlib import Path


def test_documents_list_and_detail(client, tmp_path: Path):
    sample = tmp_path / "docs-list.txt"
    sample.write_text("Document list endpoint test with bilingual metadata.", encoding="utf-8")

    r = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert r.status_code == 200

    rl = client.get("/v1/documents")
    assert rl.status_code == 200
    out = rl.json()
    assert out["total"] >= 1
    assert len(out["items"]) >= 1
    item = out["items"][0]
    assert item["doc_id"]
    assert item["file_name"]
    assert "category_path" in item

    rd = client.get(f"/v1/documents/{item['doc_id']}")
    assert rd.status_code == 200
    doc = rd.json()
    assert doc["doc_id"] == item["doc_id"]
    assert isinstance(doc["chunks"], list)


def test_categories_endpoint(client, tmp_path: Path):
    sample = tmp_path / "category.txt"
    sample.write_text("Category aggregation endpoint test.", encoding="utf-8")

    r = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert r.status_code == 200

    rc = client.get("/v1/categories")
    assert rc.status_code == 200
    out = rc.json()
    assert out["total_categories"] >= 1
    assert len(out["items"]) >= 1
    first = out["items"][0]
    assert first["category_path"]
    assert first["doc_count"] >= 1
