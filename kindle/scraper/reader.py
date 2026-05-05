"""Page-by-page text extraction from Kindle Cloud Reader."""

from __future__ import annotations

import asyncio
import io
from typing import Any

import pytesseract
import structlog
from PIL import Image

from . import ocr as kindle_ocr
from .anti_detect import humanize_mouse, jittered_delay

log = structlog.get_logger(__name__)

KINDLE_READER_URL = "https://read.amazon.com/?asin={asin}"

# Time to give Kryptonite-rendered books before sampling. The new reader does
# a "Learning reading speed..." warm-up that takes longer than the classic KCR
# bootup; sampling earlier produces blank pages.
_KRYPTONITE_WARMUP_MS = 12_000

# Header/footer chrome to crop out of full-page screenshots in Kryptonite
# mode. The Ionic-based KCR shell has a ~60px header and ~60px footer at
# 1280x900; cropping removes the title bar and the location/percent bar so
# they don't show up duplicated on every page.
_KRYPTONITE_HEADER_PX = 60
_KRYPTONITE_FOOTER_PX = 60

# Detect the new "Kryptonite" reader by its webpack manifest marker. The
# script tag `window.webpackManifest_KryptoniteReaderWebAppAssets` is set at
# top-of-document during Kryptonite boot; the classic KCR doesn't define it.
_KRYPTONITE_DETECT_JS = (
    "() => Object.keys(window).some(k => k.startsWith('webpackManifest_Kryptonite'))"
)

# Ordered list of content container selectors to try
_CONTENT_SELECTORS = [
    "#kindleReader_content",
    "#reader-column-content",
    "#column0",
    "[data-pid]",
    "#kr-renderer",
    ".kr-text-container",
]

_IFRAME_NAME = "KindleReaderIFrame"

# JS to extract visible text from the reader content area
_EXTRACT_TEXT_JS = """
() => {
    const selectors = %s;
    for (const sel of selectors) {
        const container = document.querySelector(sel);
        if (!container) continue;

        const elements = container.querySelectorAll('span, p, div');
        if (elements.length === 0) continue;

        // Group text by vertical position for paragraph detection
        const lines = [];
        let lastBottom = -Infinity;
        let currentLine = [];

        for (const el of elements) {
            const text = el.textContent.trim();
            if (!text) continue;

            const rect = el.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) continue;

            // New paragraph if vertical gap > 5px
            if (rect.top - lastBottom > 5 && currentLine.length > 0) {
                lines.push(currentLine.join(' '));
                currentLine = [];
            }
            currentLine.push(text);
            lastBottom = rect.bottom;
        }
        if (currentLine.length > 0) {
            lines.push(currentLine.join(' '));
        }
        if (lines.length > 0) {
            return lines.join('\\n');
        }
    }
    return '';
}
""" % str([s for s in _CONTENT_SELECTORS]).replace("'", '"')


async def scrape_book(
    context,
    book_asin: str,
    page_delay_min: float = 1.5,
    page_delay_max: float = 4.0,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Scrape text from a Kindle book page by page.

    Args:
        context: Playwright BrowserContext (already authenticated).
        book_asin: The ASIN of the book to scrape.
        page_delay_min: Minimum delay between page turns.
        page_delay_max: Maximum delay between page turns.
        max_pages: Stop after this many pages (None = until end).

    Returns:
        Dict with keys: title, page_count, text.
    """
    page = await context.new_page()
    url = KINDLE_READER_URL.format(asin=book_asin)
    log.info("opening_book", asin=book_asin, url=url)

    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)

    is_kryptonite = await _is_kryptonite(page)
    if is_kryptonite:
        # Kryptonite boots slower than classic KCR.
        log.info("kryptonite_detected", warmup_ms=_KRYPTONITE_WARMUP_MS)
        await page.wait_for_timeout(_KRYPTONITE_WARMUP_MS)

    content_frame = await _get_content_frame(page)
    title = await _extract_title(page, content_frame, is_kryptonite=is_kryptonite)
    log.info("book_title_extracted", title=title, mode="kryptonite" if is_kryptonite else "dom")

    # Try to navigate to the beginning
    await _navigate_to_start(page, is_kryptonite=is_kryptonite)
    await page.wait_for_timeout(2000)

    # Re-acquire content frame after navigation
    content_frame = await _get_content_frame(page)

    all_text: list[str] = []
    seen_texts: set[str] = set()
    page_count = 0
    consecutive_empty = 0
    max_consecutive_empty = 5

    while True:
        if max_pages is not None and page_count >= max_pages:
            log.info("reached_max_pages", max_pages=max_pages)
            break

        if is_kryptonite:
            text = await _extract_text_kryptonite(page)
        else:
            try:
                text = await content_frame.evaluate(_EXTRACT_TEXT_JS)
                text = text.strip() if text else ""
            except Exception as exc:
                log.warning("text_extraction_failed", page=page_count, error=str(exc))
                text = ""

            if not text:
                text = await _extract_text_via_ocr(content_frame)

        if text and text not in seen_texts:
            seen_texts.add(text)
            all_text.append(text)
            consecutive_empty = 0
            log.debug("page_extracted", page=page_count, chars=len(text))
        else:
            consecutive_empty += 1
            if consecutive_empty >= max_consecutive_empty:
                log.info(
                    "scraping_stopped_no_progress",
                    consecutive_empty=consecutive_empty,
                )
                break

        page_count += 1

        # Check if we're at the last page
        if await _is_last_page(page, content_frame):
            log.info("reached_last_page", page=page_count)
            break

        # Human-like behavior between page turns
        await humanize_mouse(page)
        delay = jittered_delay(page_delay_min, page_delay_max)
        await asyncio.sleep(delay)

        # Turn page
        await page.keyboard.press("ArrowRight")
        await page.wait_for_timeout(1500)

        # Re-acquire content frame in case it changed (no-op in Kryptonite mode)
        if not is_kryptonite:
            content_frame = await _get_content_frame(page)

    combined_text = "\n\n".join(all_text)
    log.info(
        "scraping_complete",
        pages=page_count,
        chars=len(combined_text),
        mode="kryptonite" if is_kryptonite else "dom",
    )

    await page.close()
    return {
        "title": title,
        "page_count": page_count,
        "text": combined_text,
    }


async def _is_kryptonite(page) -> bool:
    """Detect Amazon's Kryptonite reader via its webpack manifest marker."""
    try:
        return bool(await page.evaluate(_KRYPTONITE_DETECT_JS))
    except Exception as exc:
        log.warning("kryptonite_detect_failed", error=str(exc))
        return False


async def _extract_text_kryptonite(page) -> str:
    """Take a full-page screenshot, crop chrome, OCR with EasyOCR (GPU)."""
    try:
        viewport = page.viewport_size or {"width": 1280, "height": 900}
        screenshot_bytes = await page.screenshot(type="png", full_page=False)
        crop = (
            0,
            _KRYPTONITE_HEADER_PX,
            viewport["width"],
            max(0, viewport["height"] - _KRYPTONITE_FOOTER_PX),
        )
        text = kindle_ocr.ocr_image_bytes(screenshot_bytes, crop=crop).strip()
        if text:
            log.debug("kryptonite_ocr_extracted", chars=len(text))
        return text
    except Exception as exc:
        log.warning("kryptonite_ocr_failed", error=str(exc))
        return ""


async def _extract_text_via_ocr(frame) -> str:
    """Fall back to OCR when DOM extraction yields no text (image-rendered pages)."""
    try:
        # Try to find the full-page image element
        img_el = await frame.query_selector(".kg-full-page-img img")
        if not img_el:
            img_el = await frame.query_selector("#kr-renderer img")

        target = img_el if img_el else await frame.query_selector("#kr-renderer")
        if not target:
            return ""

        screenshot_bytes = await target.screenshot(type="png")
        image = Image.open(io.BytesIO(screenshot_bytes))
        text = pytesseract.image_to_string(image).strip()
        if text:
            log.debug("ocr_extracted", chars=len(text))
        return text
    except Exception as exc:
        log.warning("ocr_failed", error=str(exc))
        return ""


async def _get_content_frame(page):
    """Return the KCR iframe frame if present, otherwise the page itself."""
    for frame in page.frames:
        if _IFRAME_NAME.lower() in frame.name.lower():
            return frame
    # Try to find iframe by selector
    iframe_el = await page.query_selector('iframe[id*="Kindle"], iframe[name*="Kindle"]')
    if iframe_el:
        frame = await iframe_el.content_frame()
        if frame:
            return frame
    return page


_KRYPTONITE_CHROME_LINES = {
    "Kindle Library",
    "Learning reading speed...",
    "Learning reading speed…",
}


async def _extract_title(page, content_frame, *, is_kryptonite: bool = False) -> str:
    """Try to extract the book title from the reader UI."""
    title_selectors = [
        "#book-title",
        ".book-title-text",
        '[data-testid="book-title"]',
        "#kr-header .title",
        "h1",
    ]
    for frame in (content_frame, page):
        for sel in title_selectors:
            try:
                el = await frame.query_selector(sel)
                if el:
                    text = await el.text_content()
                    if text and text.strip():
                        return text.strip()
            except Exception:
                pass

    if is_kryptonite:
        # Kryptonite shows the title at the top of the chrome — first non-chrome
        # line of body.innerText that isn't a "Location N of M" indicator.
        try:
            body_text = await page.evaluate(
                "() => document.body && document.body.innerText || ''"
            )
        except Exception:
            body_text = ""
        for line in (body_text or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped in _KRYPTONITE_CHROME_LINES:
                continue
            if stripped.startswith("Location ") or "%" in stripped[-3:]:
                continue
            return stripped[:300]

    # Fallback: try page title
    page_title = await page.title()
    if page_title and "kindle" not in page_title.lower():
        return page_title
    return "Unknown Title"


# Set the Kryptonite scrubber bar to its minimum and emit the events Ionic
# listens to. This is the only reliable "go to beginning" path we've found
# on the new reader — Home key, chevron menu, and the URL itself all leave
# you at KCR's last-known reading position. Returns ``true`` on success so
# the Python caller can verify the affordance was present.
_KRYPTONITE_GO_TO_START_JS = r"""
() => {
    const scrubber = document.querySelector('#kr-scrubber-bar');
    if (!scrubber) return false;
    try {
        scrubber.value = 0;
        scrubber.dispatchEvent(new CustomEvent('ionChange', {detail: {value: 0}}));
        scrubber.dispatchEvent(new CustomEvent('ionInput', {detail: {value: 0}}));
        return true;
    } catch (_) {
        return false;
    }
}
"""


async def _navigate_to_start(page, *, is_kryptonite: bool = False) -> None:
    """Attempt to navigate to the beginning of the book.

    KCR remembers the last-read position per book, so a fresh ``page.goto``
    drops you back where you stopped. Both reader generations are handled:

    - Classic KCR responds to the Home key and a "Go to Beginning" menu item.
    - Kryptonite ignores both. The progress scrubber (``#kr-scrubber-bar``)
      *does* accept a programmatic ``value = 0`` plus the matching ``ionChange``
      and ``ionInput`` Ionic events — that's the reliable path we use here.
    """
    if is_kryptonite:
        try:
            ok = await page.evaluate(_KRYPTONITE_GO_TO_START_JS)
            if ok:
                # KCR repaginates after the scrubber move; give it time.
                await page.wait_for_timeout(3000)
                log.info("kryptonite_navigated_to_start")
                return
            log.warning("kryptonite_scrubber_missing")
        except Exception as exc:
            log.warning("kryptonite_navigate_failed", error=str(exc))
        # Fall through to the classic path as a last resort.

    # Classic KCR — Home key, then chevron-menu "Go to Beginning".
    try:
        await page.keyboard.press("Home")
        await page.wait_for_timeout(1000)
    except Exception:
        pass

    try:
        menu_btn = await page.query_selector(
            '#kr-header-menu, [aria-label="Menu"], .header-menu-btn'
        )
        if menu_btn:
            await menu_btn.click()
            await page.wait_for_timeout(500)
            beginning_btn = await page.query_selector(
                'text="Go to Beginning", text="Beginning"'
            )
            if beginning_btn:
                await beginning_btn.click()
                await page.wait_for_timeout(1000)
    except Exception:
        pass


async def _is_last_page(page, content_frame) -> bool:
    """Check whether we've reached the last page of the book."""
    # Check for hidden right-turn area
    try:
        right_turn = await page.query_selector(
            '#kr-right-turn-area, .right-page-turn'
        )
        if right_turn:
            is_hidden = await right_turn.evaluate(
                "(el) => { const s = getComputedStyle(el); "
                "return s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0'; }"
            )
            if is_hidden:
                return True
    except Exception:
        pass

    # Check for 100% progress indicator
    try:
        for frame in (content_frame, page):
            progress = await frame.query_selector(
                '.progress-bar, #progress-indicator, [role="progressbar"]'
            )
            if progress:
                value = await progress.get_attribute("aria-valuenow")
                if value and float(value) >= 100:
                    return True
                text = await progress.text_content()
                if text and "100%" in text:
                    return True
    except Exception:
        pass

    return False
