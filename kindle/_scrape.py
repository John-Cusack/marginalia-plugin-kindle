"""Shared scrape helper — runs Playwright scrape and writes the text to disk."""

from __future__ import annotations

from typing import Any

import structlog

from ._paths import COOKIE_PATH, EXTRACTED_DIR, text_path_for
from .scraper.auth import ensure_authenticated
from .scraper.browser import create_browser
from .scraper.reader import scrape_book
from .session.cookies import CookieStore

log = structlog.get_logger(__name__)


async def scrape_to_disk(
    book_asin: str,
    *,
    force_reauth: bool = False,
    page_delay_min: float = 1.5,
    page_delay_max: float = 4.0,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Scrape KCR for ``book_asin`` and persist the text under EXTRACTED_DIR.

    Returns ``{"title", "page_count", "text", "output_path"}``. ``output_path``
    is ``None`` when the scrape produced no text — in that case nothing is
    written to disk, so a retry will scrape fresh rather than getting stuck on
    a 0-byte cache file.
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

        text: str = result["text"]
        output_path: str | None = None
        if text.strip():
            EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
            path = text_path_for(book_asin)
            path.write_text(text, encoding="utf-8")
            output_path = str(path)
            log.info("text_saved", path=output_path, chars=len(text))
        else:
            log.warning(
                "scrape_produced_no_text",
                asin=book_asin,
                pages=result.get("page_count"),
            )

        return {
            "title": result["title"],
            "page_count": result["page_count"],
            "text": text,
            "output_path": output_path,
        }
    finally:
        if session is not None:
            await session.close()
