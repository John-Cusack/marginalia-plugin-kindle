"""Regression tests for the _scrape helper's empty-text behavior."""

from __future__ import annotations

import pytest

from kindle import _scrape
from kindle._paths import text_path_for


class _FakeBrowserSession:
    def __init__(self):
        self.context = object()
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.fixture
def patch_pipeline(monkeypatch, tmp_path):
    """Wire scrape_to_disk to a controllable fake pipeline.

    Returns a closure that lets each test set the result scrape_book returns
    and inspect what landed on disk.
    """
    monkeypatch.setattr(_scrape, "EXTRACTED_DIR", tmp_path)
    monkeypatch.setattr(_scrape, "text_path_for", lambda a: tmp_path / f"{a}.txt")

    state = {"scrape_result": None, "auth_called": False}

    async def fake_create_browser(headless=False):
        return _FakeBrowserSession()

    async def fake_ensure_authenticated(session, cookie_store, force_reauth=False):
        state["auth_called"] = True

    async def fake_scrape_book(context, **kwargs):
        return state["scrape_result"]

    monkeypatch.setattr(_scrape, "create_browser", fake_create_browser)
    monkeypatch.setattr(_scrape, "ensure_authenticated", fake_ensure_authenticated)
    monkeypatch.setattr(_scrape, "scrape_book", fake_scrape_book)

    def configure(scrape_result):
        state["scrape_result"] = scrape_result
        return state

    return configure


@pytest.mark.asyncio
async def test_writes_file_when_text_present(patch_pipeline, tmp_path):
    patch_pipeline({"title": "Some Book", "page_count": 12, "text": "hello world"})

    result = await _scrape.scrape_to_disk("B003NX6Z3W")

    assert result["text"] == "hello world"
    assert result["output_path"] == str(tmp_path / "B003NX6Z3W.txt")
    assert (tmp_path / "B003NX6Z3W.txt").read_text() == "hello world"


@pytest.mark.asyncio
async def test_does_not_write_when_text_empty(patch_pipeline, tmp_path):
    """Regression: 0.2.0 wrote a 0-byte file on empty scrape, sticking the cache."""
    patch_pipeline({"title": "Some Book", "page_count": 0, "text": ""})

    result = await _scrape.scrape_to_disk("BFAILFAILX")

    assert result["text"] == ""
    assert result["output_path"] is None
    assert not (tmp_path / "BFAILFAILX.txt").exists()


@pytest.mark.asyncio
async def test_does_not_write_when_text_whitespace_only(patch_pipeline, tmp_path):
    patch_pipeline({"title": "Some Book", "page_count": 3, "text": "   \n\n  "})

    result = await _scrape.scrape_to_disk("BFAILFAILX")

    assert result["output_path"] is None
    assert not (tmp_path / "BFAILFAILX.txt").exists()
