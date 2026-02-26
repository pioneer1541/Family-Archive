import base64
import datetime as dt
import os
import re

from sqlalchemy.orm import Session

from app import crud, models
from app.config import get_settings
from app.logging_utils import get_logger, sanitize_log_context
from app.services.ingestion import enqueue_ingestion_job

try:
    from google.oauth2.credentials import Credentials as GoogleCredentials
    from google.auth.transport.requests import Request as GoogleRequest
    from googleapiclient.discovery import build as google_build
except Exception:  # pragma: no cover - optional runtime dependency
    GoogleCredentials = None
    GoogleRequest = None
    google_build = None


settings = get_settings()
logger = get_logger(__name__)

_GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

_GMAIL_HEALTH_MESSAGES: dict[str, str] = {
    "gmail_token_not_found": "Token file missing — re-authorize Gmail",
    "gmail_token_invalid": "Token file corrupted or expired — re-authorize Gmail",
    "gmail_token_refresh_failed": "OAuth refresh failed — re-authorize Gmail",
    "gmail_credentials_not_found": "credentials.json missing",
    "gmail_service_init_failed": "Gmail API initialization failed",
    "missing_google_gmail_dependencies": "Google client libraries not installed",
}


def get_gmail_health() -> dict:
    """Return a health dict for the Gmail integration.

    Returns {"status": "ok", "detail": ""} on success, or
    {"status": <error_code>, "detail": <human message>} on failure.
    Logs a warning whenever the health check fails.
    """
    _, err = _gmail_service()
    if not err:
        return {"status": "ok", "detail": ""}
    detail = _GMAIL_HEALTH_MESSAGES.get(str(err), str(err))
    logger.warning(
        "gmail_health_check_failed",
        extra=sanitize_log_context({"error": err, "detail": detail}),
    )
    return {"status": str(err), "detail": detail}


def _header_value(headers: list, name: str) -> str:
    key = str(name or "").strip().lower()
    for h in headers or []:
        hn = str((h or {}).get("name") or "").strip().lower()
        if hn == key:
            return str((h or {}).get("value") or "").strip()
    return ""


def _walk_parts(payload: dict) -> list[dict]:
    out: list[dict] = []
    stack = [payload or {}]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        out.append(node)
        for p in node.get("parts") or []:
            if isinstance(p, dict):
                stack.append(p)
    return out


def _part_header_value(part: dict, name: str) -> str:
    key = str(name or "").strip().lower()
    headers = (part or {}).get("headers") or []
    for item in headers:
        row = item or {}
        h_name = str(row.get("name") or "").strip().lower()
        if h_name == key:
            return str(row.get("value") or "").strip()
    return ""


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", str(name or "").strip())
    cleaned = cleaned.strip("._")
    return cleaned or "attachment.bin"


def _allowed_extension(name: str) -> bool:
    fn = str(name or "").strip().lower()
    if "." not in fn:
        return False
    ext = fn.rsplit(".", 1)[-1].strip().lower()
    allowed = {str(x or "").strip().lower().lstrip(".") for x in settings.mail_allowed_extensions}
    return ext in allowed


def _photo_max_bytes() -> int:
    return max(0, int(settings.photo_max_size_mb or 0)) * 1024 * 1024


def _is_photo_name(name: str) -> bool:
    fn = str(name or "").strip().lower()
    if "." not in fn:
        return False
    ext = fn.rsplit(".", 1)[-1].strip().lower()
    photo_exts = {str(x or "").strip().lower().lstrip(".") for x in settings.photo_file_extensions}
    return ext in photo_exts


def _is_photo_too_large(name: str, size_bytes: int) -> bool:
    cap = _photo_max_bytes()
    if cap <= 0:
        return False
    if not _is_photo_name(name):
        return False
    return int(size_bytes or 0) > cap


def _inline_name_regex() -> re.Pattern[str] | None:
    raw = str(settings.mail_inline_name_patterns or "").strip()
    if not raw:
        return None
    try:
        return re.compile(raw, flags=re.IGNORECASE)
    except Exception:
        return None


def _part_content_disposition(part: dict) -> str:
    value = _part_header_value(part, "Content-Disposition")
    if value:
        return value
    return str((part or {}).get("disposition") or "").strip()


def _part_has_content_id(part: dict) -> bool:
    content_id = _part_header_value(part, "Content-ID")
    if content_id:
        return True
    return bool(str((part or {}).get("contentId") or "").strip())


def _is_inline_asset_part(part: dict, file_name: str) -> bool:
    disposition = _part_content_disposition(part).lower()
    inline_by_disposition = "inline" in disposition
    has_content_id = _part_has_content_id(part)
    name = str(file_name or "").strip()
    name_regex = _inline_name_regex()
    name_suspected_inline = bool(name_regex.search(name)) if name_regex else False
    mime_type = str((part or {}).get("mimeType") or "").strip().lower()
    is_image_like = _is_photo_name(name) or mime_type.startswith("image/")

    if bool(settings.mail_require_attachment_disposition):
        if "attachment" not in disposition:
            return True
    if not bool(settings.mail_skip_inline_images):
        return False
    if name_suspected_inline:
        return True
    if is_image_like and (inline_by_disposition or has_content_id):
        return True
    return False


def _gmail_service():
    if (GoogleCredentials is None) or (GoogleRequest is None) or (google_build is None):
        return (None, "missing_google_gmail_dependencies")
    if not os.path.exists(settings.mail_token_path):
        return (None, "gmail_token_not_found")
    if not os.path.exists(settings.mail_credentials_path):
        return (None, "gmail_credentials_not_found")
    try:
        creds = GoogleCredentials.from_authorized_user_file(settings.mail_token_path, _GMAIL_SCOPES)
    except Exception:
        return (None, "gmail_token_invalid")
    try:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
            try:
                with open(settings.mail_token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
            except Exception:
                pass
    except Exception:
        return (None, "gmail_token_refresh_failed")
    try:
        return (google_build("gmail", "v1", credentials=creds, cache_discovery=False), "")
    except Exception:
        return (None, "gmail_service_init_failed")


def _event(
    db: Session,
    *,
    message_id: str,
    subject: str,
    from_addr: str,
    attachment_name: str,
    attachment_path: str,
    status: str,
    detail: str = "",
    sync_run_id: str | None = None,
) -> None:
    db.add(
        models.MailIngestionEvent(
            message_id=str(message_id or ""),
            subject=str(subject or "")[:512],
            from_addr=str(from_addr or "")[:512],
            attachment_name=str(attachment_name or "")[:512],
            attachment_path=str(attachment_path or ""),
            status=str(status or "created")[:32],
            detail=str(detail or "")[:240],
            sync_run_id=(str(sync_run_id or "").strip()[:36] or None),
            created_at=dt.datetime.now(dt.UTC),
        )
    )


def poll_mailbox_and_enqueue(db: Session, *, max_results: int | None = None, sync_run_id: str | None = None) -> dict:
    service, err = _gmail_service()
    if service is None:
        return {
            "polled_messages": 0,
            "processed_messages": 0,
            "downloaded_attachments": 0,
            "queued": False,
            "queue_mode": "none",
            "job_id": "",
            "detail": str(err or "mail_service_unavailable"),
        }

    cap = int(max_results or settings.mail_max_results)
    if cap < 1:
        cap = 1
    if cap > 100:
        cap = 100

    downloaded_paths: list[str] = []
    processed = 0
    polled = 0

    try:
        query = str(settings.mail_query or "").strip() or "has:attachment newer_than:30d"
        resp = service.users().messages().list(userId="me", q=query, maxResults=cap).execute()
        msgs = resp.get("messages") or []
        polled = len(msgs)
        for msg in msgs:
            message_id = str((msg or {}).get("id") or "").strip()
            if not message_id:
                continue
            if db.get(models.MailProcessedMessage, message_id) is not None:
                continue

            subject = ""
            from_addr = ""
            try:
                full = service.users().messages().get(userId="me", id=message_id, format="full").execute()
                payload = full.get("payload") or {}
                headers = payload.get("headers") or []
                subject = _header_value(headers, "Subject")
                from_addr = _header_value(headers, "From")
                parts = _walk_parts(payload)
            except Exception:
                _event(
                    db,
                    message_id=message_id,
                    subject=subject,
                    from_addr=from_addr,
                    attachment_name="",
                    attachment_path="",
                    status="failed",
                    detail="message_read_failed",
                    sync_run_id=sync_run_id,
                )
                db.add(models.MailProcessedMessage(message_id=message_id))
                processed += 1
                continue

            matched = 0
            for part in parts:
                fn = str(part.get("filename") or "").strip()
                if (not fn) or (not _allowed_extension(fn)):
                    continue
                if _is_inline_asset_part(part, fn):
                    _event(
                        db,
                        message_id=message_id,
                        subject=subject,
                        from_addr=from_addr,
                        attachment_name=fn,
                        attachment_path="",
                        status="skipped",
                        detail="inline_asset",
                        sync_run_id=sync_run_id,
                    )
                    continue
                body = part.get("body") or {}
                if not (body.get("attachmentId") or body.get("data")):
                    continue

                raw = ""
                try:
                    if body.get("data"):
                        raw = str(body.get("data") or "")
                    else:
                        aid = str(body.get("attachmentId") or "")
                        att = service.users().messages().attachments().get(userId="me", messageId=message_id, id=aid).execute()
                        raw = str((att or {}).get("data") or "")
                except Exception:
                    _event(
                        db,
                        message_id=message_id,
                        subject=subject,
                        from_addr=from_addr,
                        attachment_name=fn,
                        attachment_path="",
                        status="failed",
                        detail="attachment_download_failed",
                        sync_run_id=sync_run_id,
                    )
                    continue

                if not raw:
                    _event(
                        db,
                        message_id=message_id,
                        subject=subject,
                        from_addr=from_addr,
                        attachment_name=fn,
                        attachment_path="",
                        status="failed",
                        detail="attachment_empty",
                        sync_run_id=sync_run_id,
                    )
                    continue

                try:
                    now = dt.datetime.now(dt.UTC)
                    save_dir = os.path.join(settings.mail_attachment_root, now.strftime("%Y"), now.strftime("%m"))
                    os.makedirs(save_dir, exist_ok=True)
                    safe_name = _safe_filename(fn)
                    local_path = os.path.realpath(os.path.join(save_dir, f"{message_id}_{safe_name}"))
                    bin_data = base64.urlsafe_b64decode(raw + "===")
                    if _is_photo_too_large(fn, len(bin_data)):
                        _event(
                            db,
                            message_id=message_id,
                            subject=subject,
                            from_addr=from_addr,
                            attachment_name=fn,
                            attachment_path="",
                            status="skipped",
                            detail="photo_too_large",
                            sync_run_id=sync_run_id,
                        )
                        continue
                    with open(local_path, "wb") as out:
                        out.write(bin_data)
                    downloaded_paths.append(local_path)
                    matched += 1
                    _event(
                        db,
                        message_id=message_id,
                        subject=subject,
                        from_addr=from_addr,
                        attachment_name=fn,
                        attachment_path=local_path,
                        status="downloaded",
                        detail="ok",
                        sync_run_id=sync_run_id,
                    )
                except Exception:
                    _event(
                        db,
                        message_id=message_id,
                        subject=subject,
                        from_addr=from_addr,
                        attachment_name=fn,
                        attachment_path="",
                        status="failed",
                        detail="attachment_save_failed",
                        sync_run_id=sync_run_id,
                    )

            if matched <= 0:
                _event(
                    db,
                    message_id=message_id,
                    subject=subject,
                    from_addr=from_addr,
                    attachment_name="",
                    attachment_path="",
                    status="skipped",
                    detail="no_supported_attachments",
                    sync_run_id=sync_run_id,
                )
            db.add(models.MailProcessedMessage(message_id=message_id))
            processed += 1

        unique_paths: list[str] = []
        seen: set[str] = set()
        for p in downloaded_paths:
            rp = os.path.realpath(str(p or "").strip())
            if (not rp) or (rp in seen):
                continue
            seen.add(rp)
            unique_paths.append(rp)

        queue_mode = "none"
        job_id = ""
        queued = False
        enqueue_paths = crud.filter_ignored_paths(db, unique_paths)
        if enqueue_paths:
            job = crud.create_ingestion_job(db, enqueue_paths)
            queue_mode = enqueue_ingestion_job(job.id)
            job_id = job.id
            queued = True
        else:
            db.commit()

        logger.info(
            "mail_poll_completed",
            extra=sanitize_log_context(
                {
                    "status": "ok",
                    "polled_messages": polled,
                    "processed_messages": processed,
                    "downloaded_attachments": len(unique_paths),
                    "ignored_paths": max(0, len(unique_paths) - len(enqueue_paths)),
                    "queued": queued,
                    "job_id": job_id,
                }
            ),
        )
        return {
            "polled_messages": polled,
            "processed_messages": processed,
            "downloaded_attachments": len(unique_paths),
            "downloaded_paths": unique_paths,
            "queued_paths": enqueue_paths,
            "queued": queued,
            "queue_mode": queue_mode,
            "job_id": job_id,
            "detail": "ok",
        }
    except Exception:
        db.rollback()
        return {
            "polled_messages": polled,
            "processed_messages": processed,
            "downloaded_attachments": 0,
            "downloaded_paths": [],
            "queued_paths": [],
            "queued": False,
            "queue_mode": "none",
            "job_id": "",
            "detail": "mail_poll_failed",
        }
