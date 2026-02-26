import datetime as dt
from pathlib import Path

from app import models
from app.services import sync_run as sync_service


def _make_doc(path: str, *, status: str = "pending") -> models.Document:
    now = dt.datetime.now(dt.UTC)
    return models.Document(
        source_path=path,
        file_name=Path(path).name,
        file_ext="pdf",
        file_size=128,
        sha256="a" * 64,
        status=status,
        doc_lang="en",
        title_en="Doc",
        title_zh="文档",
        summary_en="",
        summary_zh="",
        category_label_en="Archive Misc",
        category_label_zh="归档杂项",
        category_path="archive/misc",
        created_at=now,
        updated_at=now,
    )


def test_start_sync_run_returns_run_id_and_sources(client, monkeypatch, tmp_path: Path):
    sample = tmp_path / "nas_sync_sample.pdf"
    sample.write_bytes(b"pdf")

    def _fake_nas(db, **kwargs):
        return {
            "candidate_files": 1,
            "changed_files": 1,
            "queued": True,
            "job_id": "nas-job-1",
            "queued_paths": [str(sample)],
        }

    def _fake_mail(db, **kwargs):
        db.add(
            models.MailIngestionEvent(
                message_id="m-1",
                subject="bill",
                from_addr="billing@example.com",
                attachment_name="mail_bill.pdf",
                attachment_path="",
                status="skipped",
                detail="no_supported_attachments",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        db.flush()
        return {
            "polled_messages": 1,
            "processed_messages": 1,
            "downloaded_attachments": 0,
            "queued": False,
            "job_id": "",
        }

    monkeypatch.setattr(sync_service, "run_nas_scan", _fake_nas)
    monkeypatch.setattr(sync_service, "poll_mailbox_and_enqueue", _fake_mail)

    r = client.post("/v1/sync/runs", json={})
    assert r.status_code == 200
    out = r.json()
    assert out["run_id"]
    assert out["nas"]["queued"] is True
    assert out["mail"]["polled_messages"] == 1


def test_sync_run_detail_updates_item_stage_from_documents(client, monkeypatch, tmp_path: Path):
    sample = tmp_path / "sync_doc_status.pdf"
    sample.write_bytes(b"doc")

    def _fake_nas(db, **kwargs):
        return {
            "candidate_files": 1,
            "changed_files": 1,
            "queued": True,
            "job_id": "nas-job-2",
            "queued_paths": [str(sample)],
        }

    monkeypatch.setattr(sync_service, "run_nas_scan", _fake_nas)
    monkeypatch.setattr(sync_service, "poll_mailbox_and_enqueue", lambda db, **kwargs: {"queued": False, "job_id": ""})

    start = client.post("/v1/sync/runs", json={}).json()
    run_id = start["run_id"]

    from app.db import SessionLocal

    db = SessionLocal()
    try:
        doc = _make_doc(str(sample), status="processing")
        db.add(doc)
        db.commit()
    finally:
        db.close()

    detail = client.get(f"/v1/sync/runs/{run_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["summary"]["processing"] >= 1
    assert any(item["stage"] == "processing" for item in body["items"])


def test_sync_last_returns_latest_finished_run(client, monkeypatch, tmp_path: Path):
    sample1 = tmp_path / "r1.pdf"
    sample2 = tmp_path / "r2.pdf"
    sample1.write_bytes(b"1")
    sample2.write_bytes(b"2")

    seq = [str(sample1), str(sample2)]

    def _fake_nas(db, **kwargs):
        path = seq.pop(0)
        return {
            "candidate_files": 1,
            "changed_files": 1,
            "queued": True,
            "job_id": "",
            "queued_paths": [path],
        }

    monkeypatch.setattr(sync_service, "run_nas_scan", _fake_nas)
    monkeypatch.setattr(sync_service, "poll_mailbox_and_enqueue", lambda db, **kwargs: {"queued": False, "job_id": ""})

    run1 = client.post("/v1/sync/runs", json={}).json()["run_id"]
    run2 = client.post("/v1/sync/runs", json={}).json()["run_id"]

    client.get(f"/v1/sync/runs/{run1}")
    client.get(f"/v1/sync/runs/{run2}")

    last = client.get("/v1/sync/last")
    assert last.status_code == 200
    out = last.json()
    assert out["last_run_id"] == run2
    assert out["last_run_status"] in {"running", "completed", "failed"}


def test_sync_run_summary_counts(client, monkeypatch, tmp_path: Path):
    sample_ok = tmp_path / "ok.pdf"
    sample_fail = tmp_path / "fail.pdf"
    sample_ok.write_bytes(b"ok")
    sample_fail.write_bytes(b"fail")

    def _fake_nas(db, **kwargs):
        return {
            "candidate_files": 2,
            "changed_files": 2,
            "queued": True,
            "job_id": "",
            "queued_paths": [str(sample_ok), str(sample_fail)],
        }

    monkeypatch.setattr(sync_service, "run_nas_scan", _fake_nas)
    monkeypatch.setattr(sync_service, "poll_mailbox_and_enqueue", lambda db, **kwargs: {"queued": False, "job_id": ""})

    run_id = client.post("/v1/sync/runs", json={}).json()["run_id"]

    from app.db import SessionLocal

    db = SessionLocal()
    try:
        db.add(_make_doc(str(sample_ok), status="completed"))
        db.add(_make_doc(str(sample_fail), status="failed"))
        db.commit()
    finally:
        db.close()

    detail = client.get(f"/v1/sync/runs/{run_id}")
    assert detail.status_code == 200
    out = detail.json()
    assert out["summary"]["total"] >= 2
    assert out["summary"]["completed"] >= 1
    assert out["summary"]["failed"] >= 1
    assert out["summary"]["terminal_count"] >= 2
    assert out["summary"]["progress_pct"] >= 90
    assert out["summary"]["is_active"] is False


def test_sync_run_is_active_true_when_processing_exists(client, monkeypatch, tmp_path: Path):
    sample = tmp_path / "active.pdf"
    sample.write_bytes(b"active")

    def _fake_nas(db, **kwargs):
        return {
            "candidate_files": 1,
            "changed_files": 1,
            "queued": True,
            "job_id": "",
            "queued_paths": [str(sample)],
        }

    monkeypatch.setattr(sync_service, "run_nas_scan", _fake_nas)
    monkeypatch.setattr(sync_service, "poll_mailbox_and_enqueue", lambda db, **kwargs: {"queued": False, "job_id": ""})

    run_id = client.post("/v1/sync/runs", json={}).json()["run_id"]
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        db.add(_make_doc(str(sample), status="processing"))
        db.commit()
    finally:
        db.close()

    out = client.get(f"/v1/sync/runs/{run_id}").json()
    assert out["status"] == "running"
    assert out["summary"]["is_active"] is True
    assert out["summary"]["active_count"] >= 1
