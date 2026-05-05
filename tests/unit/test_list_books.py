"""Tests for kindle.list_books handler."""

from __future__ import annotations

import pytest

pytest.importorskip("research_engine")

from kindle.tools.list_books import handler as list_books


class _FakeIngestion:
    def __init__(self, by_asin: dict[str, list[dict]]):
        self._by_asin = by_asin
        self.calls: list[str] = []

    async def find_existing(self, *, source=None, source_pattern=None):
        self.calls.append(source_pattern or source)
        return list(self._by_asin.get(source_pattern, []))


@pytest.mark.asyncio
async def test_lists_disk_files_filtered_by_asin_shape(tmp_path, monkeypatch):
    (tmp_path / "B003NX6Z3W.txt").write_text("a", encoding="utf-8")
    (tmp_path / "B0090NUP8K.txt").write_text("b", encoding="utf-8")
    (tmp_path / "not-an-asin.txt").write_text("c", encoding="utf-8")
    (tmp_path / "lowercase00.txt").write_text("d", encoding="utf-8")
    monkeypatch.setattr("kindle.tools.list_books.EXTRACTED_DIR", tmp_path)

    result = await list_books(include_ingested=False, ingestion=None)

    asins = [b["asin"] for b in result["books"]]
    assert asins == ["B003NX6Z3W", "B0090NUP8K"]
    assert result["count"] == 2
    assert result["ingested_count"] == 0


@pytest.mark.asyncio
async def test_joins_corpus_documents(tmp_path, monkeypatch):
    (tmp_path / "B003NX6Z3W.txt").write_text("a", encoding="utf-8")
    (tmp_path / "B0090NUP8K.txt").write_text("b", encoding="utf-8")
    monkeypatch.setattr("kindle.tools.list_books.EXTRACTED_DIR", tmp_path)

    fake = _FakeIngestion({
        "B003NX6Z3W": [
            {
                "document_id": "doc-A",
                "title": "Ethics",
                "document_type": "kindle_book",
                "source": str(tmp_path / "B003NX6Z3W.txt"),
                "ingested_at": "2026-01-01T00:00:00+00:00",
                "passage_count": 301,
                "metadata": {},
            }
        ],
        # B0090NUP8K not in corpus
    })

    result = await list_books(include_ingested=True, ingestion=fake)

    by_asin = {b["asin"]: b for b in result["books"]}
    assert by_asin["B003NX6Z3W"]["ingested"] is True
    assert by_asin["B003NX6Z3W"]["documents"][0]["document_id"] == "doc-A"
    assert by_asin["B0090NUP8K"]["ingested"] is False
    assert by_asin["B0090NUP8K"]["documents"] == []
    assert result["ingested_count"] == 1
    assert sorted(fake.calls) == ["B003NX6Z3W", "B0090NUP8K"]


@pytest.mark.asyncio
async def test_skip_corpus_lookup_when_disabled(tmp_path, monkeypatch):
    (tmp_path / "B003NX6Z3W.txt").write_text("a", encoding="utf-8")
    monkeypatch.setattr("kindle.tools.list_books.EXTRACTED_DIR", tmp_path)

    fake = _FakeIngestion({"B003NX6Z3W": [{"document_id": "doc-A"}]})
    result = await list_books(include_ingested=False, ingestion=fake)

    assert result["books"][0]["ingested"] is False
    assert fake.calls == []  # we asked it not to look


@pytest.mark.asyncio
async def test_handles_empty_extracted_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("kindle.tools.list_books.EXTRACTED_DIR", tmp_path)
    result = await list_books(include_ingested=True, ingestion=None)
    assert result == {"count": 0, "ingested_count": 0, "books": []}
