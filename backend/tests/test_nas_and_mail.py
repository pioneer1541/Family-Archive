import base64
from pathlib import Path

from app.services import mail_ingest, nas


class _FakeExec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeMessages:
    def __init__(self, full_payload):
        self._full_payload = full_payload

    def list(self, userId: str, q: str, maxResults: int):
        return _FakeExec({"messages": [{"id": "mid_001"}]})

    def get(self, userId: str, id: str, format: str):
        return _FakeExec(self._full_payload)

    def attachments(self):
        return self


class _FakeUsers:
    def __init__(self, full_payload):
        self._messages = _FakeMessages(full_payload)

    def messages(self):
        return self._messages


class _FakeGmailService:
    def __init__(self, full_payload):
        self._users = _FakeUsers(full_payload)

    def users(self):
        return self._users


def test_nas_scan_endpoint_incremental(client, tmp_path: Path, monkeypatch):
    root = tmp_path / "nas_docs"
    root.mkdir(parents=True, exist_ok=True)
    doc = root / "bill.txt"
    doc.write_text("Water bill amount due.", encoding="utf-8")

    monkeypatch.setattr(nas.settings, "nas_default_source_dir", str(root))
    monkeypatch.setattr(nas.settings, "nas_allowed_extensions", ["txt"])

    r1 = client.post("/v1/ingestion/nas/scan", json={"paths": [str(root)], "recursive": True, "max_files": 100})
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["paths"] == [str(root.resolve())]
    assert body1["candidate_files"] == 1
    assert body1["changed_files"] == 1
    assert body1["queued"] is True
    assert body1["job_id"]

    r2 = client.post("/v1/ingestion/nas/scan", json={"paths": [str(root)], "recursive": True, "max_files": 100})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["candidate_files"] == 1
    assert body2["changed_files"] == 0
    assert body2["queued"] is False

    outside = tmp_path / "outside_docs"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "outside.txt").write_text("Should be ignored by NAS root guard.", encoding="utf-8")
    r_outside = client.post("/v1/ingestion/nas/scan", json={"paths": [str(outside)], "recursive": True, "max_files": 100})
    assert r_outside.status_code == 200
    body_outside = r_outside.json()
    assert body_outside["paths"] == [str(root.resolve())]
    assert body_outside["candidate_files"] == 1
    assert body_outside["changed_files"] == 0
    assert body_outside["queued"] is False

    doc.write_text("Water bill amount due. Updated copy.", encoding="utf-8")
    r3 = client.post("/v1/ingestion/nas/scan", json={"paths": [str(root)], "recursive": True, "max_files": 100})
    assert r3.status_code == 200
    body3 = r3.json()
    assert body3["candidate_files"] == 1
    assert body3["changed_files"] == 1
    assert body3["queued"] is True


def test_mail_poll_downloads_attachment_and_creates_event(client, tmp_path: Path, monkeypatch):
    payload_txt = base64.urlsafe_b64encode(b"Energy bill notice for April.").decode().rstrip("=")
    full_payload = {
        "payload": {
            "headers": [
                {"name": "Subject", "value": "April Bill"},
                {"name": "From", "value": "billing@example.com"},
            ],
            "parts": [
                {
                    "filename": "april_bill.txt",
                    "headers": [{"name": "Content-Disposition", "value": "attachment; filename=\"april_bill.txt\""}],
                    "body": {"data": payload_txt},
                }
            ],
        }
    }

    nas_root = tmp_path / "nas_mount"
    nas_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(mail_ingest, "_gmail_service", lambda: (_FakeGmailService(full_payload), ""))
    monkeypatch.setattr(mail_ingest.settings, "mail_attachment_root", str(tmp_path / "mail_data"))
    monkeypatch.setattr(mail_ingest.settings, "mail_allowed_extensions", ["txt"])
    monkeypatch.setattr(mail_ingest.settings, "mail_query", "has:attachment")
    monkeypatch.setattr(mail_ingest.settings, "mail_max_results", 10)
    r_cfg = client.patch(
        "/v1/settings",
        json={"nas_default_source_dir": str(nas_root), "mail_attachment_subdir": "email_attachments"},
    )
    assert r_cfg.status_code == 200

    r = client.post("/v1/mail/poll", json={"max_results": 5})
    assert r.status_code == 200
    out = r.json()
    assert out["polled_messages"] == 1
    assert out["processed_messages"] == 1
    assert out["downloaded_attachments"] == 1
    assert out["queued"] is True
    assert out["job_id"]

    rs = client.post("/v1/search", json={"query": "energy bill notice", "top_k": 5, "query_lang": "en", "ui_lang": "en"})
    assert rs.status_code == 200
    hits = rs.json().get("hits") or []
    assert len(hits) >= 1
    assert any(str(item.get("source_type") or "") == "mail" for item in hits)
    assert all("/" in str(item.get("category_path") or "") for item in hits)

    ev = client.get("/v1/mail/events?limit=20&offset=0")
    assert ev.status_code == 200
    items = ev.json().get("items") or []
    assert len(items) >= 1
    assert any(str(it.get("status") or "") == "downloaded" for it in items)
    assert any("email_attachments" in str(it.get("attachment_path") or "") for it in items)

    r2 = client.post("/v1/mail/poll", json={"max_results": 5})
    assert r2.status_code == 200
    out2 = r2.json()
    assert out2["processed_messages"] == 0
    assert out2["downloaded_attachments"] == 0


def test_mail_poll_skips_oversized_photo_attachment(client, tmp_path: Path, monkeypatch):
    payload_photo = base64.urlsafe_b64encode(b"x" * (1024 * 1024 + 200)).decode().rstrip("=")
    full_payload = {
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Large image"},
                {"name": "From", "value": "camera@example.com"},
            ],
            "parts": [
                {
                    "filename": "large_photo.jpg",
                    "headers": [{"name": "Content-Disposition", "value": "attachment; filename=\"large_photo.jpg\""}],
                    "body": {"data": payload_photo},
                }
            ],
        }
    }

    monkeypatch.setattr(mail_ingest, "_gmail_service", lambda: (_FakeGmailService(full_payload), ""))
    monkeypatch.setattr(mail_ingest.settings, "mail_attachment_root", str(tmp_path / "mail_data"))
    monkeypatch.setattr(mail_ingest.settings, "mail_allowed_extensions", ["jpg"])
    monkeypatch.setattr(mail_ingest.settings, "photo_file_extensions", ["jpg"])
    monkeypatch.setattr(mail_ingest.settings, "photo_max_size_mb", 1)
    monkeypatch.setattr(mail_ingest.settings, "mail_query", "has:attachment")
    monkeypatch.setattr(mail_ingest.settings, "mail_max_results", 10)

    r = client.post("/v1/mail/poll", json={"max_results": 5})
    assert r.status_code == 200
    out = r.json()
    assert out["polled_messages"] == 1
    assert out["processed_messages"] == 1
    assert out["downloaded_attachments"] == 0
    assert out["queued"] is False

    ev = client.get("/v1/mail/events?limit=20&offset=0")
    assert ev.status_code == 200
    items = ev.json().get("items") or []
    assert any(str(it.get("detail") or "") == "photo_too_large" for it in items)


def test_mail_poll_skips_inline_image_asset(client, tmp_path: Path, monkeypatch):
    payload_photo = base64.urlsafe_b64encode(b"photo-bytes").decode().rstrip("=")
    full_payload = {
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Inline asset"},
                {"name": "From", "value": "notice@example.com"},
            ],
            "parts": [
                {
                    "filename": "image003.png",
                    "mimeType": "image/png",
                    "headers": [
                        {"name": "Content-Disposition", "value": "inline; filename=\"image003.png\""},
                        {"name": "Content-ID", "value": "<image003@cid>"},
                    ],
                    "body": {"data": payload_photo},
                },
                {
                    "filename": "invoice.pdf",
                    "mimeType": "application/pdf",
                    "headers": [{"name": "Content-Disposition", "value": "attachment; filename=\"invoice.pdf\""}],
                    "body": {"data": payload_photo},
                },
            ],
        }
    }

    monkeypatch.setattr(mail_ingest, "_gmail_service", lambda: (_FakeGmailService(full_payload), ""))
    monkeypatch.setattr(mail_ingest.settings, "mail_attachment_root", str(tmp_path / "mail_data"))
    monkeypatch.setattr(mail_ingest.settings, "mail_allowed_extensions", ["png", "pdf"])
    monkeypatch.setattr(mail_ingest.settings, "mail_require_attachment_disposition", True)
    monkeypatch.setattr(mail_ingest.settings, "mail_skip_inline_images", True)
    monkeypatch.setattr(mail_ingest.settings, "mail_inline_name_patterns", r"image\d{3,4}|logo|signature|smime")
    monkeypatch.setattr(mail_ingest.settings, "mail_query", "has:attachment")
    monkeypatch.setattr(mail_ingest.settings, "mail_max_results", 10)

    r = client.post("/v1/mail/poll", json={"max_results": 5})
    assert r.status_code == 200
    out = r.json()
    assert out["downloaded_attachments"] == 1

    ev = client.get("/v1/mail/events?limit=20&offset=0")
    assert ev.status_code == 200
    items = ev.json().get("items") or []
    assert any(str(it.get("attachment_name") or "") == "image003.png" and str(it.get("detail") or "") == "inline_asset" for it in items)


def test_connectivity_health_reports_nas_read_write(client, tmp_path: Path):
    nas_root = tmp_path / "nas_for_connectivity"
    nas_root.mkdir(parents=True, exist_ok=True)

    # 通过 settings 写入运行时配置，模拟用户在 UI 中配置 NAS 目录。
    r_patch = client.patch("/v1/settings", json={"nas_default_source_dir": str(nas_root)})
    assert r_patch.status_code == 200

    r_conn = client.get("/v1/health/connectivity")
    assert r_conn.status_code == 200
    body = r_conn.json()
    nas = body.get("nas") or {}
    assert nas.get("path") == str(nas_root)
    assert nas.get("readable") is True
    assert nas.get("writable") is True
    assert nas.get("ok") is True
    assert nas.get("error") is None
