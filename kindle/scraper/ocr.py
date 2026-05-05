"""GPU-accelerated OCR for Kryptonite-rendered Kindle pages.

Kryptonite is Amazon's new in-browser KCR renderer (rolling out 2025+). It
draws book text via WebGL/non-DOM surfaces specifically to defeat DOM-based
extraction. The only path to text is OCR on a full-page screenshot.

We use EasyOCR with GPU acceleration. The reader is constructed lazily and
cached at module level — first call pays a ~3s init cost (model load into
VRAM) and ~64MB of one-time model downloads on first install. Subsequent
calls run at ~0.3-1s per page on a modern consumer GPU.
"""

from __future__ import annotations

import io
from typing import Any

import structlog
from PIL import Image

log = structlog.get_logger(__name__)


# Cached reader. Module-global so the model stays resident across page
# extractions during a single book scrape.
_reader: Any = None


def _get_reader(*, languages: list[str] | None = None, gpu: bool = True) -> Any:
    """Return a cached EasyOCR reader, constructing it on first call."""
    global _reader
    if _reader is not None:
        return _reader
    import easyocr  # heavy import; defer

    langs = languages or ["en"]
    log.info("easyocr_init", languages=langs, gpu=gpu)
    _reader = easyocr.Reader(langs, gpu=gpu, verbose=False)
    return _reader


def reset_reader() -> None:
    """Drop the cached reader. Useful in tests."""
    global _reader
    _reader = None


def ocr_image_bytes(image_bytes: bytes, *, crop: tuple[int, int, int, int] | None = None) -> str:
    """OCR a screenshot. ``crop`` is ``(left, top, right, bottom)`` in pixels."""
    image = Image.open(io.BytesIO(image_bytes))
    if crop is not None:
        image = image.crop(crop)
    # EasyOCR accepts a numpy array or a path; converting to numpy avoids
    # writing a temp file.
    import numpy as np  # transitive via opencv-python-headless / scikit-image

    arr = np.array(image)
    reader = _get_reader()
    results = reader.readtext(arr, detail=0, paragraph=True)
    return "\n".join(results)
