"""MCP tool handler for scraping a Kindle book from Cloud Reader."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..scraper.browser import create_browser
from ..scraper.auth import ensure_authenticated
from ..scraper.reader import scrape_book
from ..session.cookies import CookieStore

log = logging.getLogger(__name__)

PLUGIN_DATA_DIR = Path.home() / ".marginalia" / "plugins" / "kindle"
COOKIE_PATH = PLUGIN_DATA_DIR / "cookies.json"
EXTRACTED_DIR = PLUGIN_DATA_DIR / "extracted"


async def tool_handler(
    container: Any,
    *,
    book_asin: str,
    force_reauth: bool = False,
    page_delay_min: float = 1.5,
    page_delay_max: float = 4.0,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Scrape the full text of a Kindle book from Cloud Reader.

    Args:
        container: Plugin container (injected by the plugin system).
        book_asin: The ASIN of the book to scrape.
        force_reauth: Force re-authentication even if cookies exist.
        page_delay_min: Minimum delay between page turns (seconds).
        page_delay_max: Maximum delay between page turns (seconds).
        max_pages: Maximum number of pages to scrape (None = all).

    Returns:
        Dict with status, title, page count, text preview, and output path.
    """
    session = None
    try:
        cookie_store = CookieStore(COOKIE_PATH)

        session = await create_browser(headless=False)
        await ensure_authenticated(session, cookie_store, force_reauth=force_reauth)

        result = await scrape_book(
            session.context,
            book_asin=book_asin,
            page_delay_min=page_delay_min,
            page_delay_max=page_delay_max,
            max_pages=max_pages,
        )

        # Save extracted text to disk
        EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
        output_path = EXTRACTED_DIR / f"{book_asin}.txt"
        output_path.write_text(result["text"], encoding="utf-8")
        log.info("Saved extracted text to %s", output_path)

        return {
            "status": "success",
            "title": result["title"],
            "asin": book_asin,
            "page_count": result["page_count"],
            "text_length": len(result["text"]),
            "text_preview": result["text"][:500],
            "output_path": str(output_path),
        }

    except TimeoutError as exc:
        log.error("Timeout during scrape of %s: %s", book_asin, exc)
        return {
            "status": "error",
            "error_type": "timeout",
            "message": str(exc),
            "asin": book_asin,
        }
    except Exception as exc:
        log.error("Failed to scrape %s: %s", book_asin, exc, exc_info=True)
        return {
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "asin": book_asin,
        }
    finally:
        if session is not None:
            await session.close()
