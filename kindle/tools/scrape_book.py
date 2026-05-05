"""kindle.scrape_book — scrape a Kindle book to disk (no ingestion)."""

from __future__ import annotations

import structlog
from research_engine.plugins.sdk import tool

from .._scrape import scrape_to_disk

log = structlog.get_logger(__name__)


@tool(
    id="kindle.scrape_book",
    description=(
        "Scrape the full text of a Kindle book from Cloud Reader by ASIN and save it "
        "to ~/.marginalia/plugins/kindle/extracted/{asin}.txt. Does NOT ingest into the "
        "corpus — use kindle.ingest_book for that."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "book_asin": {
                "type": "string",
                "description": "The ASIN of the book (e.g. 'B003NX6Z3W').",
            },
            "force_reauth": {
                "type": "boolean",
                "default": False,
                "description": "Force a fresh Amazon login, ignoring cached cookies.",
            },
            "page_delay_min": {"type": "number", "default": 1.5},
            "page_delay_max": {"type": "number", "default": 4.0},
            "max_pages": {
                "type": ["integer", "null"],
                "default": None,
                "description": "Stop after N pages (null = scrape entire book).",
            },
        },
        "required": ["book_asin"],
    },
)
async def handler(
    book_asin: str,
    force_reauth: bool = False,
    page_delay_min: float = 1.5,
    page_delay_max: float = 4.0,
    max_pages: int | None = None,
    **_clients,
) -> dict:
    try:
        result = await scrape_to_disk(
            book_asin,
            force_reauth=force_reauth,
            page_delay_min=page_delay_min,
            page_delay_max=page_delay_max,
            max_pages=max_pages,
        )
        text = result["text"]
        if not text.strip():
            return {
                "status": "error",
                "error_type": "empty_text",
                "message": (
                    f"Scrape produced no text for {book_asin}. The book may be "
                    "canvas-rendered (KF8 fixed-layout) or KCR may have shown a "
                    "captcha/sign-in page instead of the book."
                ),
                "asin": book_asin,
                "page_count": result.get("page_count"),
            }
        return {
            "status": "success",
            "asin": book_asin,
            "title": result["title"],
            "page_count": result["page_count"],
            "text_length": len(text),
            "text_preview": text[:500],
            "output_path": result["output_path"],
        }
    except TimeoutError as exc:
        log.error("scrape_timeout", asin=book_asin, error=str(exc))
        return {
            "status": "error",
            "error_type": "timeout",
            "message": str(exc),
            "asin": book_asin,
        }
    except Exception as exc:
        log.exception("scrape_failed", asin=book_asin, error=str(exc))
        return {
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "asin": book_asin,
        }
