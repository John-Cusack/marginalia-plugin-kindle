"""Tests for kindle.check_book handler."""

from __future__ import annotations

import pytest

pytest.importorskip("research_engine")

from kindle.tools.check_book import handler as check_book


class _FakeIngestion:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    async def find_existing(self, *, source=None, source_pattern=None):
        self.calls.append({"source": source, "source_pattern": source_pattern})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.asyncio
async def test_reports_disk_and_corpus_hit(tmp_path, monkeypatch):
    asin = "B003NX6Z3W"
    # Redirect EXTRACTED_DIR so the fake "on-disk" file lives in tmp_path.
    fake_path = tmp_path / f"{asin}.txt"
    fake_path.write_text("hello", encoding="utf-8")
    monkeypatch.setattr("kindle._paths.EXTRACTED_DIR", tmp_path)
    monkeypatch.setattr("kindle.tools.check_book.text_path_for", lambda a: tmp_path / f"{a}.txt")

    fake = _FakeIngestion(response=[
        {
            "document_id": "doc-1",
            "title": "Doc",
            "document_type": "kindle_book",
            "source": str(fake_path),
            "ingested_at": "2026-01-01T00:00:00+00:00",
            "passage_count": 42,
            "metadata": {"asin": asin},
        }
    ])

    result = await check_book(book_asin=asin, ingestion=fake)

    assert result["asin"] == asin
    assert result["on_disk"] is True
    assert result["disk"]["size_bytes"] == 5
    assert result["ingested"] is True
    assert len(result["documents"]) == 1
    assert fake.calls == [{"source": None, "source_pattern": asin}]


@pytest.mark.asyncio
async def test_reports_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("kindle.tools.check_book.text_path_for", lambda a: tmp_path / f"{a}.txt")
    fake = _FakeIngestion(response=[])

    result = await check_book(book_asin="BFAKEFAKE0", ingestion=fake)

    assert result["on_disk"] is False
    assert result["disk"] is None
    assert result["ingested"] is False
    assert result["documents"] == []


@pytest.mark.asyncio
async def test_handles_corpus_lookup_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("kindle.tools.check_book.text_path_for", lambda a: tmp_path / f"{a}.txt")
    fake = _FakeIngestion(response=RuntimeError("db down"))

    result = await check_book(book_asin="B003NX6Z3W", ingestion=fake)

    assert result["ingested"] is False
    assert "warning" in result
    assert "db down" in result["warning"]


@pytest.mark.asyncio
async def test_no_ingestion_client_means_no_corpus_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr("kindle.tools.check_book.text_path_for", lambda a: tmp_path / f"{a}.txt")
    result = await check_book(book_asin="B003NX6Z3W", ingestion=None)
    assert result["ingested"] is False
    assert result["documents"] == []
