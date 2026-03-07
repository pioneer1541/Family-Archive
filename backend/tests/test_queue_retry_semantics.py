from pathlib import Path

from app.services import nas
from app.services.ingestion import build_retry_error_code, parse_retry_meta


def test_retry_error_code_roundtrip():
    code = build_retry_error_code("worker_exception:TimeoutError", retry_count=2, max_retries=3)
    retry_count, max_retries = parse_retry_meta(code)
    assert retry_count == 2
    assert max_retries == 3


def test_ingestion_job_response_contains_retry_fields(client, tmp_path: Path):
    sample = tmp_path / "retry-fields.txt"
    sample.write_text("Retry metadata check.", encoding="utf-8")

    r = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert r.status_code == 200
    body = r.json()
    assert "retry_count" in body
    assert "max_retries" in body
    assert body["retry_count"] == 0
    assert int(body["max_retries"]) >= 0

    g = client.get(f"/v1/ingestion/jobs/{body['job_id']}")
    assert g.status_code == 200
    got = g.json()
    assert "retry_count" in got
    assert "max_retries" in got


def test_failed_job_sets_error_code(client):
    missing = "/tmp/fkv-not-exists-queue-retry-test.txt"
    r = client.post("/v1/ingestion/jobs", json={"file_paths": [missing]})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "job_all_failed"


def test_retry_failed_job_creates_new_job(client):
    missing = "/tmp/fkv-not-exists-queue-retry-endpoint-test.txt"
    r = client.post("/v1/ingestion/jobs", json={"file_paths": [missing]})
    assert r.status_code == 200
    source = r.json()
    assert source["status"] == "failed"

    rr = client.post(f"/v1/ingestion/jobs/{source['job_id']}/retry")
    assert rr.status_code == 200
    retried = rr.json()
    assert retried["job_id"] != source["job_id"]
    assert retried["input_paths"] == [missing]
    assert retried["queue_mode"] == "sync"
    assert retried["status"] == "failed"


def test_retry_non_retryable_job_rejected(client, tmp_path: Path):
    sample = tmp_path / "ok-file.txt"
    sample.write_text("hello queue retry", encoding="utf-8")
    r = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"

    rr = client.post(f"/v1/ingestion/jobs/{body['job_id']}/retry")
    assert rr.status_code == 400
    assert rr.json()["detail"] == "job_not_retryable"


def test_delete_job_ignores_paths_for_future_enqueue(client, tmp_path: Path, monkeypatch):
    sample = tmp_path / "queue_delete_sample.txt"
    sample.write_text("queue delete and ignore path", encoding="utf-8")

    created = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert created.status_code == 200
    job_id = created.json()["job_id"]

    deleted = client.delete(f"/v1/ingestion/jobs/{job_id}")
    assert deleted.status_code == 200
    out = deleted.json()
    assert out["deleted"] is True
    assert out["job_id"] == job_id
    assert int(out["ignored_paths"]) >= 1

    missing = client.get(f"/v1/ingestion/jobs/{job_id}")
    assert missing.status_code == 404

    blocked = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "all_paths_ignored"

    # Mock resolve_source_root to return nas source type
    monkeypatch.setattr(
        "app.services.nas.resolve_source_root",
        lambda db: ("nas", str(tmp_path))
    )
    monkeypatch.setattr(nas.settings, "nas_allowed_extensions", ["txt"])
    scanned = client.post("/v1/ingestion/nas/scan", json={"paths": [str(tmp_path)], "recursive": True, "max_files": 100})
    assert scanned.status_code == 200
    scan_out = scanned.json()
    assert scan_out["candidate_files"] >= 1
    assert scan_out["changed_files"] == 0
    assert scan_out["queued"] is False
