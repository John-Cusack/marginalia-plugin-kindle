"""Shared scrape helper — runs Playwright scrape and writes the text to disk."""

from __future__ import annotations

import logging
from typing import Any

from ._paths import COOKIE_PATH, EXTRACTED_DIR, text_path_for
from .scraper.auth import ensure_authenticated
from .scraper.browser import create_browser
from .scraper.reader import scrape_book
from .session.cookies import CookieStore

log = logging.getLogger(__name__)


async def scrape_to_disk(
    book_asin: str,
    *,
    force_reauth: bool = False,
    page_delay_min: float = 1.5,
    page_delay_max: float = 4.0,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Scrape KCR for ``book_asin`` and persist the text under EXTRACTED_DIR.

    Returns ``{"title", "page_count", "text", "output_path"}``.
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

        EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
        output_path = text_path_for(book_asin)
        output_path.write_text(result["text"], encoding="utf-8")
        log.info("Saved extracted text to %s", output_path)

        return {
            "title": result["title"],
            "page_count": result["page_count"],
            "text": result["text"],
            "output_path": str(output_path),
        }
    finally:
        if session is not None:
            await session.close()
