"""Tests for the OCR module — covers caching, crop arg, and reader plumbing."""

from __future__ import annotations

import io

import pytest

pytest.importorskip("PIL")

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from PIL import Image

from kindle.scraper import ocr


def _png_bytes(width: int = 200, height: int = 100, color=(255, 255, 255)) -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _FakeReader:
    def __init__(self):
        self.calls: list[dict] = []
        self.return_value: list[str] = ["hello world"]

    def readtext(self, image, *, detail: int = 1, paragraph: bool = False):
        # `image` is a numpy array — record its shape so tests can assert on
        # crop behaviour without needing easyocr installed.
        shape = getattr(image, "shape", None)
        self.calls.append({"shape": shape, "detail": detail, "paragraph": paragraph})
        return list(self.return_value)


@pytest.fixture(autouse=True)
def _reset_reader():
    ocr.reset_reader()
    yield
    ocr.reset_reader()


def _install_fake_reader(monkeypatch) -> _FakeReader:
    fake = _FakeReader()
    monkeypatch.setattr(ocr, "_get_reader", lambda *a, **kw: fake)
    return fake


def test_ocr_image_bytes_joins_results(monkeypatch):
    fake = _install_fake_reader(monkeypatch)
    fake.return_value = ["line one", "line two", "line three"]

    out = ocr.ocr_image_bytes(_png_bytes())

    assert out == "line one\nline two\nline three"
    assert len(fake.calls) == 1
    assert fake.calls[0]["paragraph"] is True
    assert fake.calls[0]["detail"] == 0


def test_ocr_image_bytes_applies_crop(monkeypatch):
    fake = _install_fake_reader(monkeypatch)
    # Source: 400x300, crop the top half → 400x150
    ocr.ocr_image_bytes(_png_bytes(400, 300), crop=(0, 0, 400, 150))

    shape = fake.calls[0]["shape"]
    # numpy shape is (rows, cols, channels) — rows=height, cols=width
    assert shape[:2] == (150, 400)


def test_ocr_image_bytes_no_crop_keeps_full_image(monkeypatch):
    fake = _install_fake_reader(monkeypatch)
    ocr.ocr_image_bytes(_png_bytes(320, 240))
    assert fake.calls[0]["shape"][:2] == (240, 320)


def test_reset_reader_clears_cache():
    # _reader is None by default; set a sentinel and reset.
    ocr._reader = object()  # type: ignore[attr-defined]
    ocr.reset_reader()
    assert ocr._reader is None  # type: ignore[attr-defined]


def test_concurrent_local_reader_initialization_loads_only_once(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    duplicate = threading.Event()
    instances = []

    def construct(*args, **kwargs):
        instance = _FakeReader()
        instances.append(instance)
        if len(instances) > 1:
            duplicate.set()
        entered.set()
        assert release.wait(3)
        return instance

    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(Reader=construct))

    def second():
        second_started.set()
        return ocr._get_reader()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(ocr._get_reader)
        assert entered.wait(2)
        later = pool.submit(second)
        try:
            assert second_started.wait(2)
            assert not duplicate.wait(0.1)
        finally:
            release.set()
        assert first.result() is later.result()
    assert len(instances) == 1
