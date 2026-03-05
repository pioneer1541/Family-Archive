import math
from pathlib import Path

from PIL import Image

from app.services.ocr_fallback import IMAGE_EXTS


def is_image_path(path: str) -> bool:
    ext = Path(str(path or "")).suffix.lower().lstrip(".")
    return ext in IMAGE_EXTS


def _alpha(idx: int, n: int) -> float:
    return math.sqrt(1.0 / n) if idx == 0 else math.sqrt(2.0 / n)


def _dct_basis(n: int, keep: int) -> list[list[float]]:
    out: list[list[float]] = []
    for k in range(keep):
        row = []
        for x in range(n):
            row.append(math.cos(((2 * x + 1) * k * math.pi) / (2 * n)))
        out.append(row)
    return out


def compute_image_phash(path: str, *, hash_size: int = 8, highfreq_factor: int = 4) -> str:
    size = int(hash_size) * int(highfreq_factor)
    if size <= 0:
        return ""

    try:
        with Image.open(path) as raw:
            img = raw.convert("L").resize((size, size), Image.Resampling.LANCZOS)
            px = list(img.getdata())
    except Exception:
        return ""

    mat = [px[i * size : (i + 1) * size] for i in range(size)]
    keep = int(hash_size)
    if keep <= 0:
        return ""

    cos_table = _dct_basis(size, keep)

    # Separable 2D DCT: first along columns, then along rows; only keep top-left keep x keep.
    row_dct: list[list[float]] = [[0.0] * keep for _ in range(size)]
    for x in range(size):
        for v in range(keep):
            s = 0.0
            basis = cos_table[v]
            for y in range(size):
                s += float(mat[x][y]) * basis[y]
            row_dct[x][v] = _alpha(v, size) * s

    coeff: list[list[float]] = [[0.0] * keep for _ in range(keep)]
    for u in range(keep):
        basis = cos_table[u]
        au = _alpha(u, size)
        for v in range(keep):
            s = 0.0
            for x in range(size):
                s += row_dct[x][v] * basis[x]
            coeff[u][v] = au * s

    flat = [coeff[u][v] for u in range(keep) for v in range(keep)]
    if not flat:
        return ""

    # Exclude DC term from median reference.
    body = flat[1:] if len(flat) > 1 else flat
    ref = sorted(body)[len(body) // 2]
    bits = [1 if val > ref else 0 for val in flat]

    out = 0
    for b in bits:
        out = (out << 1) | int(b)
    width = (len(bits) + 3) // 4
    return format(out, f"0{width}x")


def hamming_distance(hash_a: str, hash_b: str) -> int:
    a = str(hash_a or "").strip().lower()
    b = str(hash_b or "").strip().lower()
    if not a or not b:
        return 10**9
    try:
        ia = int(a, 16)
        ib = int(b, 16)
    except Exception:
        return 10**9
    return int((ia ^ ib).bit_count())
