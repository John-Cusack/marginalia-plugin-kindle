"""kindle.ingest_book — scrape (if needed) and ingest a Kindle book into the corpus."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from research_engine.plugins.sdk import tool
from research_engine.services.ingestion.chunking.prose_window import ProseWindowChunker

from .._paths import text_path_for
from .._scrape import scrape_to_disk

log = logging.getLogger(__name__)


@tool(
    id="kindle.ingest_book",
    description=(
        "Scrape a Kindle book by ASIN (or reuse the on-disk cache) and ingest it into "
        "the corpus as document_type='kindle_book'. Idempotent: returns the existing "
        "document_id if the book has already been ingested."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "book_asin": {
                "type": "string",
                "description": "The ASIN of the book to ingest.",
            },
            "rescrape": {
                "type": "boolean",
                "default": False,
                "description": (
                    "If true, scrape fresh from Cloud Reader even if a cached text file "
                    "exists on disk."
                ),
            },
            "force_reingest": {
                "type": "boolean",
                "default": False,
                "description": (
                    "If true, ingest a new document even if one already exists for this "
                    "ASIN. (Will create a duplicate; useful for re-chunking after a rescrape.)"
                ),
            },
            "force_reauth": {"type": "boolean", "default": False},
            "page_delay_min": {"type": "number", "default": 1.5},
            "page_delay_max": {"type": "number", "default": 4.0},
            "max_pages": {"type": ["integer", "null"], "default": None},
        },
        "required": ["book_asin"],
    },
)
async def handler(
    book_asin: str,
    rescrape: bool = False,
    force_reingest: bool = False,
    force_reauth: bool = False,
    page_delay_min: float = 1.5,
    page_delay_max: float = 4.0,
    max_pages: int | None = None,
    ingestion=None,
    **_clients,
) -> dict:
    if ingestion is None:
        return {"status": "error", "error_type": "config", "message": "Ingestion client unavailable. Plugin needs permissions.ingest=true."}

    text_path = text_path_for(book_asin)
    source = str(text_path.resolve())

    # Idempotency check
    if not force_reingest:
        existing = await ingestion.find_existing(source=source)
        if existing:
            doc = existing[0]
            return {
                "status": "already_ingested",
                "asin": book_asin,
                "document_id": doc["document_id"],
                "title": doc["title"],
                "passage_count": doc["passage_count"],
                "ingested_at": doc["ingested_at"],
                "source": doc["source"],
            }

    # Acquire text — reuse cached file unless rescrape requested or no cache exists
    title: str
    page_count: int | None = None
    if rescrape or not text_path.exists():
        scraped = await scrape_to_disk(
            book_asin,
            force_reauth=force_reauth,
            page_delay_min=page_delay_min,
            page_delay_max=page_delay_max,
            max_pages=max_pages,
        )
        text = scraped["text"]
        title = scraped["title"]
        page_count = scraped["page_count"]
    else:
        text = text_path.read_text(encoding="utf-8")
        # Best-effort title from the first non-empty line; the scrape title is
        # not retained on disk, so this is the cleanest fallback.
        title = next(
            (line.strip() for line in text.splitlines() if line.strip()),
            f"Kindle Book {book_asin}",
        )[:200]

    if not text.strip():
        return {
            "status": "error",
            "error_type": "empty_text",
            "message": f"No text available for {book_asin} (file: {text_path}).",
            "asin": book_asin,
        }

    metadata = {
        "asin": book_asin,
        "kindle_title": title,
        "scraped_at": datetime.now(UTC).isoformat(),
        "source_url": f"https://read.amazon.com/?asin={book_asin}",
        "char_count": len(text),
    }
    if page_count is not None:
        metadata["page_count"] = page_count

    chunker = ProseWindowChunker()
    drafts = await chunker.chunk(text, metadata)

    result = await ingestion.ingest_drafts(
        title=title,
        document_type="kindle_book",
        passage_drafts=drafts,
        source=source,
        metadata=metadata,
    )

    return {
        "status": "ingested",
        "asin": book_asin,
        "title": title,
        "document_id": result["document_id"],
        "passage_count": result["passage_count"],
        "source": source,
    }
