import base64
import io
import os
import re
from pathlib import Path

import requests
from sqlalchemy.orm import Session

from app.config import get_settings
from app.runtime_config import get_model_setting

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency
    Image = None

try:
    import pypdfium2 as pdfium
except Exception:  # pragma: no cover - optional dependency
    pdfium = None


settings = get_settings()
_MULTISPACE = re.compile(r"[ \t]+")
_MULTILINE = re.compile(r"\n{3,}")


def _in_test_mode() -> bool:
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


def _normalize_text(text: str) -> str:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_MULTISPACE.sub(" ", line).strip() for line in raw.split("\n")]
    clean = "\n".join(line for line in lines if line)
    return _MULTILINE.sub("\n\n", clean).strip()


def _to_b64_image_bytes(raw: bytes) -> str:
    if not raw:
        return ""
    return base64.b64encode(raw).decode("utf-8")


def _call_vl(
    images_b64: list[str], *, prompt: str, db: Session | None = None
) -> str:
    if (
        (not images_b64)
        or _in_test_mode()
        or (not bool(settings.ingestion_vl_fallback_enabled))
    ):
        return ""
    try:
        url = settings.ollama_base_url.rstrip("/") + "/api/chat"
        payload = {
            "model": get_model_setting("vl_extract_model", db),
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are OCR extraction assistant. Extract visible document text faithfully. "
                        "Do not invent content. Keep numbers, dates, amounts and headings. "
                        "Return plain text only."
                    ),
                },
                {
                    "role": "user",
                    "content": str(prompt or "Extract text from image."),
                    "images": images_b64,
                },
            ],
            "options": {"temperature": 0.0},
        }
        r = requests.post(
            url, json=payload, timeout=max(6, int(settings.vl_timeout_sec))
        )
        r.raise_for_status()
        body = r.json() if hasattr(r, "json") else {}
        msg = body.get("message") if isinstance(body, dict) else {}
        return _normalize_text(str((msg or {}).get("content") or ""))
    except Exception:
        return ""


def _read_image_as_b64(path: str) -> str:
    raw_path = str(path or "").strip()
    if (not raw_path) or (not os.path.exists(raw_path)):
        return ""
    try:
        with open(raw_path, "rb") as f:
            raw = f.read()
        return _to_b64_image_bytes(raw)
    except Exception:
        return ""


def _render_pdf_page_b64(path: str, page_index: int, *, dpi: int = 180) -> str:
    if pdfium is None or Image is None:
        return ""
    raw_path = str(path or "").strip()
    if (not raw_path) or (not os.path.exists(raw_path)):
        return ""
    try:
        doc = pdfium.PdfDocument(raw_path)
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
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            return _to_b64_image_bytes(buf.getvalue())
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


def extract_image_text_with_vl(path: str, db: Session | None = None) -> str:
    ext = Path(str(path or "")).suffix.lower().lstrip(".")
    if ext not in {"jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff", "heic"}:
        return ""
    image_b64 = _read_image_as_b64(path)
    if not image_b64:
        return ""
    return _call_vl(
        [image_b64], prompt="Extract all visible text from this document image.", db=db
    )


def extract_pdf_page_text_with_vl(
    path: str, page_index: int, *, dpi: int = 180, db: Session | None = None
) -> str:
    image_b64 = _render_pdf_page_b64(path, page_index, dpi=dpi)
    if not image_b64:
        return ""
    return _call_vl(
        [image_b64],
        prompt=f"Extract text from PDF page {int(page_index) + 1}.",
        db=db,
    )


def extract_pdf_text_with_vl(
    path: str, *, max_pages: int = 8, dpi: int = 180, db: Session | None = None
) -> str:
    if _in_test_mode():
        return ""
    raw_path = str(path or "").strip()
    if (not raw_path) or (not os.path.exists(raw_path)):
        return ""
    limit = max(1, int(max_pages))
    out: list[str] = []
    for idx in range(limit):
        text = extract_pdf_page_text_with_vl(raw_path, idx, dpi=dpi, db=db)
        if text:
            out.append(text)
    return _normalize_text("\n".join(out))
