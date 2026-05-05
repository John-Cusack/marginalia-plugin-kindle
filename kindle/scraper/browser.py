"""Playwright browser lifecycle with anti-detection configuration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from .anti_detect import random_user_agent

log = logging.getLogger(__name__)

STEALTH_INIT_SCRIPT = """
() => {
    // Remove webdriver flag
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // Fake plugins array
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client', filename: 'internal-nacl-plugin' },
        ],
    });

    // Fake languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
    });

    // Set window.chrome
    window.chrome = {
        runtime: {},
        loadTimes: function() {},
        csi: function() {},
        app: {},
    };
}
"""


@dataclass
class BrowserSession:
    """Holds Playwright objects for a browser session."""

    pw: Playwright
    browser: Browser
    context: BrowserContext

    async def close(self) -> None:
        """Shut down browser and Playwright."""
        try:
            await self.context.close()
        except Exception:
            pass
        try:
            await self.browser.close()
        except Exception:
            pass
        try:
            await self.pw.stop()
        except Exception:
            pass


async def create_browser(headless: bool = False) -> BrowserSession:
    """Launch Chromium with stealth configuration.

    Args:
        headless: Run headless (not recommended for auth — Amazon detects it).
    """
    ua = random_user_agent()
    log.info("Launching browser (headless=%s, ua=%s)", headless, ua[:50])

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
        ],
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=ua,
        locale="en-US",
        timezone_id="America/New_York",
    )
    await context.add_init_script(STEALTH_INIT_SCRIPT)

    return BrowserSession(pw=pw, browser=browser, context=context)
