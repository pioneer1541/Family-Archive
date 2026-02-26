from pathlib import Path

from app import crud


def test_tasks_list_and_create_flow(client):
    empty = client.get('/v1/tasks?limit=20&offset=0')
    assert empty.status_code == 200
    empty_out = empty.json()
    assert empty_out['total'] == 0
    assert empty_out['items'] == []

    created = client.post(
        '/v1/tasks',
        json={
            'title': 'Monthly electricity analysis',
            'task_type': 'summarize_docs',
            'doc_set': [],
            'filters': {},
        },
    )
    assert created.status_code == 200
    task_id = created.json()['task_id']

    listed = client.get('/v1/tasks?limit=20&offset=0')
    assert listed.status_code == 200
    out = listed.json()
    assert out['total'] == 1
    assert len(out['items']) == 1
    assert out['items'][0]['task_id'] == task_id
    assert out['items'][0]['status'] == 'created'


def test_task_id_is_anchored_to_document_friendly_name(client, tmp_path: Path):
    sample = tmp_path / "task_anchor_bill_2026.txt"
    sample.write_text("Annual electricity bill statement for 2026.", encoding="utf-8")
    ing = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert ing.status_code == 200

    docs = client.get("/v1/documents?limit=20&offset=0&status=completed")
    assert docs.status_code == 200
    items = docs.json().get("items") or []
    doc_id = next(it["doc_id"] for it in items if it["file_name"] == "task_anchor_bill_2026.txt")

    detail = client.get(f"/v1/documents/{doc_id}")
    assert detail.status_code == 200
    doc = detail.json()
    anchor = str(doc.get("title_en") or doc.get("title_zh") or doc.get("file_name") or "").strip()
    token = crud._slug_token(anchor, max_len=22)

    created = client.post(
        "/v1/tasks",
        json={
            "title": "Generic summary task",
            "task_type": "summarize_docs",
            "doc_set": [doc_id],
            "filters": {},
        },
    )
    assert created.status_code == 200
    task_id = created.json()["task_id"]
    assert task_id.startswith(f"task-{token}-")
