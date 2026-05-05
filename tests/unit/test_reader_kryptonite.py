"""Tests for Kryptonite detection + OCR routing in reader.py.

Playwright is not driven here. We exercise the small async helpers
(``_is_kryptonite``, ``_extract_title``, ``_extract_text_kryptonite``) with
hand-rolled fakes shaped like the page/frame interface they actually use.
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("PIL")

from PIL import Image

from kindle.scraper import reader


class _FakePage:
    def __init__(
        self,
        *,
        kryptonite: bool = False,
        body_text: str = "",
        viewport: dict | None = None,
        screenshot_bytes: bytes | None = None,
        title_str: str = "Kindle",
    ) -> None:
        self.kryptonite = kryptonite
        self.body_text = body_text
        self.viewport_size = viewport or {"width": 1280, "height": 900}
        self._screenshot = screenshot_bytes or self._make_png()
        self._title = title_str

    @staticmethod
    def _make_png(w: int = 1280, h: int = 900) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (w, h), (255, 255, 255)).save(buf, format="PNG")
        return buf.getvalue()

    async def evaluate(self, js: str):
        if "webpackManifest_Kryptonite" in js:
            return self.kryptonite
        if "document.body" in js and "innerText" in js:
            return self.body_text
        raise NotImplementedError(f"unexpected JS: {js[:80]}")

    async def screenshot(self, **kwargs):
        return self._screenshot

    async def query_selector(self, sel: str):
        return None  # no DOM matches in fake

    async def title(self):
        return self._title


@pytest.mark.asyncio
async def test_is_kryptonite_true():
    page = _FakePage(kryptonite=True)
    assert await reader._is_kryptonite(page) is True


@pytest.mark.asyncio
async def test_is_kryptonite_false():
    page = _FakePage(kryptonite=False)
    assert await reader._is_kryptonite(page) is False


@pytest.mark.asyncio
async def test_is_kryptonite_swallows_evaluate_failure():
    class Blowup:
        async def evaluate(self, js):
            raise RuntimeError("page closed")

    assert await reader._is_kryptonite(Blowup()) is False


@pytest.mark.asyncio
async def test_extract_title_kryptonite_skips_chrome_lines():
    page = _FakePage(
        kryptonite=True,
        body_text=(
            "THE SHIELD OF ACHILLES: WAR, PEACE, AND THE COURSE OF HISTORY\n"
            "Kindle Library\n"
            "THE SHIELD OF ACHILLES: WAR, PEACE, AND THE COURSE OF HISTORY\n"
            "Location 397 of 20557 ● 2%\n"
            "Learning reading speed..."
        ),
    )
    title = await reader._extract_title(page, page, is_kryptonite=True)
    assert title == "THE SHIELD OF ACHILLES: WAR, PEACE, AND THE COURSE OF HISTORY"


@pytest.mark.asyncio
async def test_extract_title_kryptonite_falls_through_to_unknown():
    page = _FakePage(
        kryptonite=True,
        body_text="Kindle Library\nLocation 1 of 100\nLearning reading speed...",
    )
    title = await reader._extract_title(page, page, is_kryptonite=True)
    assert title == "Unknown Title"


@pytest.mark.asyncio
async def test_extract_text_kryptonite_routes_to_ocr_with_crop(monkeypatch):
    captured: dict = {}

    def fake_ocr(image_bytes, *, crop=None):
        captured["bytes_len"] = len(image_bytes)
        captured["crop"] = crop
        return "  this is the page text  "

    monkeypatch.setattr(reader.kindle_ocr, "ocr_image_bytes", fake_ocr)

    page = _FakePage(viewport={"width": 1280, "height": 900})
    out = await reader._extract_text_kryptonite(page)

    assert out == "this is the page text"
    # Crop strips the documented header (60px) and footer (60px) bands.
    assert captured["crop"] == (0, 60, 1280, 840)
    assert captured["bytes_len"] > 0


@pytest.mark.asyncio
async def test_extract_text_kryptonite_returns_empty_on_ocr_error(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("ocr backend down")

    monkeypatch.setattr(reader.kindle_ocr, "ocr_image_bytes", boom)
    page = _FakePage()
    assert await reader._extract_text_kryptonite(page) == ""


class _FakeKeyboard:
    def __init__(self):
        self.calls: list[str] = []

    async def press(self, key: str):
        self.calls.append(key)


class _NavPage:
    """Page stub for _navigate_to_start that records JS evals + key presses."""

    def __init__(self, *, scrubber_returns: bool = True, eval_raises: bool = False):
        self._scrubber_returns = scrubber_returns
        self._eval_raises = eval_raises
        self.eval_calls: list[str] = []
        self.timeouts: list[int] = []
        self.keyboard = _FakeKeyboard()

    async def evaluate(self, js: str):
        self.eval_calls.append(js)
        if self._eval_raises:
            raise RuntimeError("evaluate failed")
        return self._scrubber_returns

    async def wait_for_timeout(self, ms: int):
        self.timeouts.append(ms)

    async def query_selector(self, sel: str):
        return None


@pytest.mark.asyncio
async def test_navigate_to_start_uses_scrubber_in_kryptonite_mode():
    page = _NavPage(scrubber_returns=True)
    await reader._navigate_to_start(page, is_kryptonite=True)

    assert len(page.eval_calls) == 1
    assert "#kr-scrubber-bar" in page.eval_calls[0]
    assert "ionChange" in page.eval_calls[0]
    # Got the post-scrubber repaginate wait but did NOT fall through to Home.
    assert page.timeouts == [3000]
    assert page.keyboard.calls == []


@pytest.mark.asyncio
async def test_navigate_to_start_falls_through_when_scrubber_missing():
    page = _NavPage(scrubber_returns=False)
    await reader._navigate_to_start(page, is_kryptonite=True)

    # Scrubber JS was tried, returned false → fall through to classic path.
    assert page.keyboard.calls == ["Home"]


@pytest.mark.asyncio
async def test_navigate_to_start_falls_through_on_evaluate_error():
    page = _NavPage(eval_raises=True)
    await reader._navigate_to_start(page, is_kryptonite=True)

    # Evaluate raised → swallowed → fall through to classic Home key.
    assert page.keyboard.calls == ["Home"]


@pytest.mark.asyncio
async def test_navigate_to_start_classic_mode_skips_scrubber():
    page = _NavPage(scrubber_returns=True)
    await reader._navigate_to_start(page, is_kryptonite=False)

    # is_kryptonite=False → never touches the scrubber.
    assert page.eval_calls == []
    assert page.keyboard.calls == ["Home"]
