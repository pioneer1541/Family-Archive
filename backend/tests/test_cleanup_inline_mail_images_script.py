from pathlib import Path

from app.db import SessionLocal
from app.models import Chunk, Document, DocumentStatus
from scripts import cleanup_inline_mail_images as cleanup_script


def _insert_doc(path: Path, *, file_name: str, ext: str = "png") -> str:
    db = SessionLocal()
    try:
        doc = Document(
            source_path=str(path),
            file_name=file_name,
            file_ext=ext,
            file_size=int(path.stat().st_size) if path.exists() else 0,
            sha256="c" * 64,
            status=DocumentStatus.COMPLETED.value,
            category_path="archive/old",
            category_label_en="Archive",
            category_label_zh="归档",
        )
        db.add(doc)
        db.flush()
        db.add(
            Chunk(
                document_id=doc.id,
                chunk_index=0,
                content="inline image placeholder",
                token_count=3,
                embedding_status="ready",
            )
        )
        db.commit()
        return str(doc.id)
    finally:
        db.close()


def test_cleanup_inline_mail_images_dry_run(monkeypatch, tmp_path: Path):
    mail_root = tmp_path / "mail"
    mail_root.mkdir(parents=True, exist_ok=True)
    target = mail_root / "19c7f9f2a6805c31_image003.png"
    target.write_bytes(b"png-bytes")
    _insert_doc(target, file_name=target.name)

    monkeypatch.setattr(cleanup_script.settings, "mail_attachment_root", str(mail_root))
    monkeypatch.setattr(cleanup_script.settings, "photo_file_extensions", ["png", "jpg"])
    monkeypatch.setattr(cleanup_script.settings, "mail_inline_name_patterns", r"image\d{3,4}|logo|signature|smime")
    monkeypatch.setattr(cleanup_script, "delete_records_by_point_ids", lambda *args, **kwargs: {"requested": 0, "deleted": 0})

    output = tmp_path / "report_dry.json"
    report = cleanup_script.cleanup_inline_mail_images(apply=False, days=0, output=output)
    assert int(report.get("candidate_count") or 0) == 1
    assert int(report.get("deleted_docs") or 0) == 0
    assert target.exists()


def test_cleanup_inline_mail_images_apply(monkeypatch, tmp_path: Path):
    mail_root = tmp_path / "mail_apply"
    mail_root.mkdir(parents=True, exist_ok=True)
    target = mail_root / "19c7f9f2a6805c31_image005.jpg"
    target.write_bytes(b"jpg-bytes")
    doc_id = _insert_doc(target, file_name=target.name, ext="jpg")

    monkeypatch.setattr(cleanup_script.settings, "mail_attachment_root", str(mail_root))
    monkeypatch.setattr(cleanup_script.settings, "photo_file_extensions", ["png", "jpg"])
    monkeypatch.setattr(cleanup_script.settings, "mail_inline_name_patterns", r"image\d{3,4}|logo|signature|smime")
    monkeypatch.setattr(cleanup_script, "delete_records_by_point_ids", lambda *args, **kwargs: {"requested": 1, "deleted": 1})

    output = tmp_path / "report_apply.json"
    report = cleanup_script.cleanup_inline_mail_images(apply=True, days=0, output=output)
    assert int(report.get("candidate_count") or 0) == 1
    assert int(report.get("deleted_docs") or 0) == 1
    assert int(report.get("deleted_points") or 0) == 1
    assert not target.exists()

    db = SessionLocal()
    try:
        assert db.get(Document, doc_id) is None
    finally:
        db.close()
