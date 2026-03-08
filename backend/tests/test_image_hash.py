from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFilter

from app.services.image_hash import compute_image_phash, hamming_distance

pytestmark = pytest.mark.no_db_reset


def _build_base_image(path: Path) -> None:
    img = Image.new("L", (128, 128), 255)
    draw = ImageDraw.Draw(img)
    for y in range(0, 128, 8):
        shade = 180 if (y // 8) % 2 else 220
        draw.line((0, y, 127, y), fill=shade, width=2)
    draw.rectangle((18, 28, 108, 98), outline=0, width=4)
    draw.ellipse((44, 40, 84, 80), fill=120)
    img.save(path)


def test_compute_image_phash_returns_stable_hex_hash(tmp_path: Path):
    path = tmp_path / "base.png"
    _build_base_image(path)

    h1 = compute_image_phash(str(path))
    h2 = compute_image_phash(str(path))

    assert h1 == h2
    assert len(h1) == 16
    assert all(ch in "0123456789abcdef" for ch in h1)


def test_similar_image_has_low_hamming_distance(tmp_path: Path):
    base_path = tmp_path / "base.png"
    blur_path = tmp_path / "blur.png"

    _build_base_image(base_path)
    with Image.open(base_path) as base_img:
        base_img.filter(ImageFilter.GaussianBlur(radius=0.8)).save(blur_path)

    base_hash = compute_image_phash(str(base_path))
    blur_hash = compute_image_phash(str(blur_path))

    assert base_hash
    assert blur_hash
    assert hamming_distance(base_hash, blur_hash) <= 8


def test_hamming_distance_comparison_and_invalid_input():
    assert hamming_distance("f0", "0f") == 8
    assert hamming_distance("0", "0") == 0
    assert hamming_distance("", "abcd") == 10**9
    assert hamming_distance("not-hex", "abcd") == 10**9


def test_compute_image_phash_handles_invalid_parameters_and_path(tmp_path: Path):
    missing = tmp_path / "missing.png"

    assert compute_image_phash(str(missing)) == ""
    assert compute_image_phash(str(missing), hash_size=0) == ""
    assert compute_image_phash(str(missing), hash_size=8, highfreq_factor=0) == ""
