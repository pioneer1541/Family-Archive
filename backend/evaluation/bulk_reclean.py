#!/usr/bin/env python3
"""
Bulk reclean: regenerate title_zh, title_en, summary_zh, summary_en
for all completed documents using updated prompts (proper-noun preservation).

Run inside fkv-api container:
    docker exec fkv-api python3 /app/evaluation/bulk_reclean.py

Or from repo root:
    docker exec fkv-api python3 evaluation/bulk_reclean.py
"""

import sys
import time
from pathlib import Path

# Allow running from repo root or inside container
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db import engine
from app.models import Chunk, Document
from app.services.llm_summary import (
    regenerate_friendly_name_from_summary,
    summarize_document_with_model,
)


# ─── helpers ────────────────────────────────────────────────────────────────

def _get_chunk_text(session: Session, doc_id: str, max_chars: int = 9000) -> str:
    """Reconstruct document text from stored chunks (ordered by index)."""
    rows = (
        session.execute(
            sa.select(Chunk.content)
            .where(Chunk.document_id == doc_id)
            .order_by(Chunk.chunk_index)
        )
        .scalars()
        .all()
    )
    text = "\n".join(r for r in rows if r)
    return text[:max_chars]


def _print(msg: str) -> None:
    print(msg, flush=True)


# ─── main ───────────────────────────────────────────────────────────────────

def main() -> None:
    with Session(engine) as session:
        docs = (
            session.execute(
                sa.select(Document)
                .where(Document.status == "completed")
                .order_by(Document.created_at)
            )
            .scalars()
            .all()
        )

    total = len(docs)
    _print(f"\n=== Bulk Reclean: {total} completed documents ===\n")

    ok_summary = 0
    ok_name = 0
    skip_summary = 0
    skip_name = 0
    errors: list[str] = []

    for idx, doc in enumerate(docs, 1):
        _print(f"[{idx}/{total}] {doc.file_name}  ({doc.category_path})")
        t0 = time.time()

        # ── Step 1: rebuild chunk text ────────────────────────────────────
        with Session(engine) as session:
            chunk_text = _get_chunk_text(session, doc.id)

        if not chunk_text.strip():
            _print(f"  ⚠  no chunk text — skipping summary regen")
            skip_summary += 1
            new_summary_en = doc.summary_en
            new_summary_zh = doc.summary_zh
        else:
            # ── Step 2: regenerate summary ────────────────────────────────
            result = summarize_document_with_model(
                text=chunk_text,
                title_en=doc.title_en,
                title_zh=doc.title_zh,
                category_label_en=doc.category_label_en,
                category_label_zh=doc.category_label_zh,
            )
            if result is None:
                _print(f"  ✗  summary regen failed (LLM error / quality check) — keeping old")
                errors.append(f"summary:{doc.file_name}")
                skip_summary += 1
                new_summary_en = doc.summary_en
                new_summary_zh = doc.summary_zh
            else:
                new_summary_en, new_summary_zh = result
                ok_summary += 1
                _print(f"  ✓  summary → zh: {new_summary_zh[:80]}…")

        # ── Step 3: regenerate friendly name ─────────────────────────────
        name_result = regenerate_friendly_name_from_summary(
            file_name=doc.file_name,
            category_path=doc.category_path,
            summary_en=new_summary_en,
            summary_zh=new_summary_zh,
            fallback_en=doc.title_en,
            fallback_zh=doc.title_zh,
        )
        if name_result is None:
            _print(f"  ✗  name regen failed — keeping old title")
            errors.append(f"name:{doc.file_name}")
            skip_name += 1
            new_title_en = doc.title_en
            new_title_zh = doc.title_zh
        else:
            new_title_en, new_title_zh = name_result
            ok_name += 1
            _print(f"  ✓  title → zh: {new_title_zh}  en: {new_title_en}")

        # ── Step 4: persist ───────────────────────────────────────────────
        with Session(engine) as session:
            d = session.get(Document, doc.id)
            if d is not None:
                d.summary_en = new_summary_en
                d.summary_zh = new_summary_zh
                d.title_en = new_title_en
                d.title_zh = new_title_zh
                session.commit()

        elapsed = time.time() - t0
        _print(f"  → saved  ({elapsed:.1f}s)\n")

    _print("=" * 60)
    _print(f"Done: {total} documents processed")
    _print(f"  summary regenerated : {ok_summary}  (skipped: {skip_summary})")
    _print(f"  name    regenerated : {ok_name}    (skipped: {skip_name})")
    if errors:
        _print(f"  errors ({len(errors)}): {', '.join(errors[:10])}")
    _print("=" * 60)


if __name__ == "__main__":
    main()
