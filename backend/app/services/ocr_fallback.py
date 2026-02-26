import os
import shutil
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.logging_utils import get_logger

try:
    import pytesseract
except Exception:  # pragma: no cover - optional dependency
    pytesseract = None

try:
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover - optional dependency
    Image = None
    ImageOps = None

try:
    import pypdfium2 as pdfium
except Exception:  # pragma: no cover - optional dependency
    pdfium = None


settings = get_settings()
logger = get_logger(__name__)
IMAGE_EXTS = {"png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"}


@lru_cache(maxsize=1)
def _tesseract_ready() -> bool:
    if pytesseract is None or Image is None or ImageOps is None:
        return False
    return bool(shutil.which("tesseract"))


def _normalize_text(value: str) -> str:
    lines = [str(line or "").strip() for line in str(value or "").splitlines()]
    keep = [line for line in lines if line]
    return "\n".join(keep).strip()


def _ocr_pil_image(img) -> str:
    if not _tesseract_ready():
        return ""
    if img is None:
        return ""
    try:
        base = img.convert("RGB")
        gray = ImageOps.grayscale(base)
        sharp = ImageOps.autocontrast(gray)
        text = pytesseract.image_to_string(sharp)
        return _normalize_text(text)
    except Exception:
        return ""


def _ocr_image_path(path: str) -> str:
    if not _tesseract_ready():
        return ""
    try:
        with Image.open(path) as img:
            return _ocr_pil_image(img)
    except Exception:
        return ""


def _ocr_pdf_path(path: str, *, max_pages: int, dpi: int) -> str:
    if pdfium is None or not _tesseract_ready():
        return ""
    try:
        doc = pdfium.PdfDocument(path)
    except Exception:
        return ""

    out: list[str] = []
    limit = max(1, int(max_pages))
    scale = max(1.0, float(dpi) / 72.0)
    try:
        doc_total = len(doc)
        if doc_total > limit:
            logger.warning(
                "ocr_pdf_truncated",
                extra={"path": path, "pages_total": doc_total, "pages_processed": limit},
            )
        total = min(doc_total, limit)
        for idx in range(total):
            page = doc[idx]
            try:
                bitmap = page.render(scale=scale)
                pil = bitmap.to_pil()
                text = _ocr_pil_image(pil)
                if text:
                    out.append(text)
            except Exception:
                continue
            finally:
                try:
                    page.close()
                except Exception:
                    pass
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return "\n".join(out).strip()


def extract_pdf_page_ocr_text(path: str, page_index: int, *, dpi: int = 180) -> str:
    if pdfium is None or not _tesseract_ready():
        return ""
    raw = str(path or "").strip()
    if not raw:
        return ""
    try:
        doc = pdfium.PdfDocument(raw)
    except Exception:
        return ""
    try:
        idx = int(page_index)
        if idx < 0 or idx >= len(doc):
            return ""
        page = doc[idx]
        try:
            scale = max(1.0, float(dpi) / 72.0)
            bitmap = page.render(scale=scale)
            pil = bitmap.to_pil()
            return _ocr_pil_image(pil)
        except Exception:
            return ""
        finally:
            try:
                page.close()
            except Exception:
                pass
    finally:
        try:
            doc.close()
        except Exception:
            pass


def extract_ocr_text(path: str) -> str:
    if not bool(settings.ingestion_ocr_fallback_enabled):
        return ""
    raw = str(path or "").strip()
    if not raw or not os.path.exists(raw):
        return ""
    ext = Path(raw).suffix.lower().lstrip(".")
    if ext == "pdf":
        return _ocr_pdf_path(
            raw,
            max_pages=int(settings.ingestion_ocr_pdf_max_pages),
            dpi=int(settings.ingestion_ocr_render_dpi),
        )
    if ext in IMAGE_EXTS:
        return _ocr_image_path(raw)
    return ""


def get_pdf_page_count(path: str) -> int | None:
    """Return the total page count of a PDF, or None if unreadable."""
    if pdfium is None:
        return None
    raw = str(path or "").strip()
    if not raw or not os.path.exists(raw):
        return None
    try:
        doc = pdfium.PdfDocument(raw)
        try:
            return len(doc)
        finally:
            try:
                doc.close()
            except Exception:
                pass
    except Exception:
        return None
