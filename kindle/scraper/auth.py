"""Interactive Amazon login with cookie reuse."""

from __future__ import annotations

import asyncio

import structlog

from ..session.cookies import CookieStore
from .browser import BrowserSession

log = structlog.get_logger(__name__)

KINDLE_URL = "https://read.amazon.com/"

# Selectors that indicate the user is NOT authenticated
_LOGIN_SELECTORS = [
    "#ap_email",
    'input[name="email"]',
    "#signInSubmit",
    'form[name="signIn"]',
]

# Selectors that indicate the library has loaded (authenticated)
_LIBRARY_SELECTORS = [
    "#library",
    "#kindle-library-list",
    '[id*="library"]',
    ".book-container",
    'div[data-asin]',
]


async def ensure_authenticated(
    session: BrowserSession,
    cookie_store: CookieStore,
    force_reauth: bool = False,
) -> None:
    """Make sure the browser context is authenticated with Amazon.

    Loads cached cookies, navigates to KCR, and if not logged in,
    prompts the user to log in manually in the visible browser window.
    """
    page = await session.context.new_page()

    if not force_reauth:
        cookies = cookie_store.load()
        if cookies:
            log.info("cookies_loaded", count=len(cookies))
            await session.context.add_cookies(cookies)

    await page.goto(KINDLE_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    if await _is_authenticated(page):
        log.info("already_authenticated")
        cookies = await session.context.cookies()
        cookie_store.save(cookies)
        await page.close()
        return

    log.info(
        "manual_login_required",
        message="Please log in to Amazon in the visible browser window (up to 5 minutes).",
    )
    await _wait_for_auth(page, timeout_seconds=300)

    cookies = await session.context.cookies()
    cookie_store.save(cookies)
    log.info("authenticated")
    await page.close()


async def _is_authenticated(page) -> bool:
    """Check whether the current page shows a library (authenticated) or login form."""
    for sel in _LOGIN_SELECTORS:
        if await page.query_selector(sel):
            return False
    for sel in _LIBRARY_SELECTORS:
        if await page.query_selector(sel):
            return True
    # Ambiguous — check URL as fallback
    url = page.url
    if "signin" in url.lower() or "ap/signin" in url.lower():
        return False
    # When ambiguous (no login or library selectors matched), default to
    # not authenticated so the user gets the manual login prompt.
    return False


async def _wait_for_auth(page, timeout_seconds: int = 300) -> None:
    """Poll until the user completes login or timeout is reached."""
    poll_interval = 2
    elapsed = 0
    while elapsed < timeout_seconds:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        try:
            if await _is_authenticated(page):
                return
        except Exception:
            pass
    raise TimeoutError(
        f"Amazon login was not completed within {timeout_seconds} seconds"
    )
