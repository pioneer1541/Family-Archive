#!/usr/bin/env python3
import os
import re
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.models import Chunk, Document, DocumentStatus  # noqa: E402
from app.services.friendly_name import generate_friendly_names  # noqa: E402
from app.services.source_tags import infer_source_type  # noqa: E402


def _clean(value: str) -> str:
    text = str(value or "")
    text = os.path.splitext(text)[0]
    text = re.sub(r"[_\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _looks_raw_title(doc: Document) -> bool:
    base = _clean(doc.file_name)
    zh = _clean(doc.title_zh)
    en = _clean(doc.title_en)
    if (not zh) or (not en):
        return True
    return zh == base and en == base


def run() -> int:
    db = SessionLocal()
    try:
        rows = (
            db.execute(select(Document).where(Document.status == DocumentStatus.COMPLETED.value).order_by(Document.updated_at.desc()))
            .scalars()
            .all()
        )
        changed = 0
        for doc in rows:
            if not _looks_raw_title(doc):
                continue
            chunks = (
                db.execute(select(Chunk.content).where(Chunk.document_id == doc.id).order_by(Chunk.chunk_index.asc()).limit(8))
                .scalars()
                .all()
            )
            text = "\n".join(str(x or "") for x in chunks)
            source_type = infer_source_type(doc.source_path)
            title_en, title_zh = generate_friendly_names(
                file_name=doc.file_name,
                text=text,
                category_path=doc.category_path,
                source_type=source_type,
                mail_subject="",
            )
            if (title_en == doc.title_en) and (title_zh == doc.title_zh):
                continue
            doc.title_en = title_en
            doc.title_zh = title_zh
            changed += 1
        db.commit()
        print("friendly_name_backfill", {"total": len(rows), "changed": changed})
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(run())
