from pathlib import Path

import pytest

from app.services import parsing

pytestmark = pytest.mark.no_db_reset


def test_pdf_page_chunks_prefers_page_extraction(monkeypatch, tmp_path: Path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(parsing, "_read_pdf_pages", lambda _p, max_pages=160, db=None: ["Page one", "", "Page two"])

    out = parsing.extract_page_chunks_from_path(str(pdf))
    assert out == ["Page one", "Page two"]


def test_pdf_page_chunks_fallback_to_ocr_then_split(monkeypatch, tmp_path: Path):
    pdf = tmp_path / "b.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(parsing, "_read_pdf_pages", lambda _p, max_pages=160, db=None: ["", ""])
    monkeypatch.setattr(parsing, "extract_ocr_text", lambda _p: " ".join(["word"] * 500))

    out = parsing.extract_page_chunks_from_path(str(pdf))
    assert len(out) >= 2
    assert all(part.strip() for part in out)


def test_extract_text_from_pdf_formats_pages(monkeypatch, tmp_path: Path):
    pdf = tmp_path / "c.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(parsing, "extract_page_chunks_from_path", lambda _p, max_pages=120, db=None: ["Alpha", "Beta"])

    text = parsing.extract_text_from_path(str(pdf))
    assert "[Page 1]" in text
    assert "Alpha" in text
    assert "[Page 2]" in text
    assert "Beta" in text


def test_text_extraction_and_format_conversion_for_txt_docx_xlsx(monkeypatch, tmp_path: Path):
    txt = tmp_path / "note.txt"
    txt.write_text("hello world", encoding="utf-8")

    docx = tmp_path / "note.docx"
    docx.write_bytes(b"not-used")
    xlsx = tmp_path / "table.xlsx"
    xlsx.write_bytes(b"not-used")

    monkeypatch.setattr(parsing, "_read_docx", lambda _p: "docx content")
    monkeypatch.setattr(parsing, "_read_xlsx", lambda _p, max_rows=2000: "## Sheet: Main\nA | B")

    assert parsing.extract_text_from_path(str(txt)) == "hello world"
    assert parsing.extract_text_from_path(str(docx)) == "docx content"
    assert parsing.extract_text_from_path(str(xlsx)) == "## Sheet: Main\nA | B"


def test_chunking_language_detection_and_unsupported_extension(tmp_path: Path):
    chunks = parsing.chunk_text(" ".join(["t"] * 120), target_tokens=50, overlap_tokens=10)
    assert len(chunks) >= 2

    assert parsing.detect_lang_simple("这是一个中文句子，用于语言检测") == "zh"
    assert parsing.detect_lang_simple("This is an English sentence for language detection") == "en"

    unsupported = tmp_path / "bad.bin"
    unsupported.write_bytes(b"x")
    with pytest.raises(ValueError, match="unsupported_extension"):
        parsing.extract_text_from_path(str(unsupported))
