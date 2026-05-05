"""Tests for kindle.ingest_book handler — focuses on idempotency + branching.

Actual scraping is not exercised; the handler reuses on-disk text when present
and force_reingest is false, which lets us drive it from a tmp_path without
hitting Playwright.
"""

from __future__ import annotations

import pytest

pytest.importorskip("research_engine")

from kindle.tools.ingest_book import handler as ingest_book


class _FakeIngestion:
    def __init__(self, *, existing=None, ingest_result=None):
        self._existing = existing or []
        self._ingest_result = ingest_result or {
            "document_id": "new-doc-id",
            "passage_count": 17,
        }
        self.find_calls = 0
        self.ingest_calls: list[dict] = []

    async def find_existing(self, *, source=None, source_pattern=None):
        self.find_calls += 1
        return list(self._existing)

    async def ingest_drafts(self, **kwargs):
        self.ingest_calls.append(kwargs)
        return dict(self._ingest_result)


@pytest.mark.asyncio
async def test_short_circuits_when_already_ingested(tmp_path, monkeypatch):
    asin = "B003NX6Z3W"
    text_path = tmp_path / f"{asin}.txt"
    text_path.write_text("body", encoding="utf-8")
    monkeypatch.setattr("kindle.tools.ingest_book.text_path_for", lambda a: text_path)

    fake = _FakeIngestion(existing=[{
        "document_id": "doc-1",
        "title": "Existing",
        "document_type": "kindle_book",
        "source": str(text_path),
        "ingested_at": "2026-01-01T00:00:00+00:00",
        "passage_count": 100,
        "metadata": {"asin": asin},
    }])

    result = await ingest_book(book_asin=asin, ingestion=fake)

    assert result["status"] == "already_ingested"
    assert result["document_id"] == "doc-1"
    assert fake.find_calls == 1
    assert fake.ingest_calls == []  # never called the heavy path


@pytest.mark.asyncio
async def test_ingests_when_text_on_disk_and_corpus_empty(tmp_path, monkeypatch):
    asin = "B003NX6Z3W"
    text_path = tmp_path / f"{asin}.txt"
    text_path.write_text(
        "The Ethics of Money Production\n\nFirst paragraph of the book.",
        encoding="utf-8",
    )
    monkeypatch.setattr("kindle.tools.ingest_book.text_path_for", lambda a: text_path)

    fake = _FakeIngestion()
    result = await ingest_book(book_asin=asin, ingestion=fake)

    assert result["status"] == "ingested"
    assert result["document_id"] == "new-doc-id"
    assert result["asin"] == asin

    assert len(fake.ingest_calls) == 1
    call = fake.ingest_calls[0]
    assert call["document_type"] == "kindle_book"
    # ASIN, source URL, and char_count must be on the doc-level metadata.
    md = call["metadata"]
    assert md["asin"] == asin
    assert md["source_url"] == f"https://read.amazon.com/?asin={asin}"
    assert md["char_count"] > 0
    # Title taken from the first non-empty line of the cached file.
    assert call["title"].startswith("The Ethics of Money Production")
    # Source path passed through so find_existing can match next time.
    assert call["source"] == str(text_path.resolve())
    # Some passage drafts were produced.
    assert len(call["passage_drafts"]) >= 1


@pytest.mark.asyncio
async def test_force_reingest_skips_idempotency_check(tmp_path, monkeypatch):
    asin = "B003NX6Z3W"
    text_path = tmp_path / f"{asin}.txt"
    text_path.write_text("body", encoding="utf-8")
    monkeypatch.setattr("kindle.tools.ingest_book.text_path_for", lambda a: text_path)

    fake = _FakeIngestion(existing=[{
        "document_id": "doc-1",
        "title": "x",
        "document_type": "kindle_book",
        "source": str(text_path),
        "ingested_at": "2026-01-01T00:00:00+00:00",
        "passage_count": 1,
        "metadata": {},
    }])

    result = await ingest_book(book_asin=asin, force_reingest=True, ingestion=fake)
    assert result["status"] == "ingested"
    assert fake.find_calls == 0  # short-circuited away
    assert len(fake.ingest_calls) == 1


@pytest.mark.asyncio
async def test_errors_when_ingestion_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr("kindle.tools.ingest_book.text_path_for", lambda a: tmp_path / f"{a}.txt")
    result = await ingest_book(book_asin="B003NX6Z3W", ingestion=None)
    assert result["status"] == "error"
    assert result["error_type"] == "config"


@pytest.mark.asyncio
async def test_errors_on_empty_text(tmp_path, monkeypatch):
    asin = "B003NX6Z3W"
    text_path = tmp_path / f"{asin}.txt"
    text_path.write_text("   \n\n", encoding="utf-8")
    monkeypatch.setattr("kindle.tools.ingest_book.text_path_for", lambda a: text_path)

    fake = _FakeIngestion()
    result = await ingest_book(book_asin=asin, ingestion=fake)
    assert result["status"] == "error"
    assert result["error_type"] == "empty_text"
