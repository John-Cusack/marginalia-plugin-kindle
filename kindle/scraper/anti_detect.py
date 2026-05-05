"""Stealth utilities for avoiding automation detection."""

from __future__ import annotations

import random

USER_AGENTS = [
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]


def random_user_agent() -> str:
    """Return a random realistic user agent string."""
    return random.choice(USER_AGENTS)


async def humanize_mouse(page) -> None:
    """Perform random mouse movements to appear human-like."""
    viewport = page.viewport_size
    if not viewport:
        return
    for _ in range(random.randint(2, 5)):
        x = random.randint(100, viewport["width"] - 100)
        y = random.randint(100, viewport["height"] - 100)
        steps = random.randint(5, 15)
        await page.mouse.move(x, y, steps=steps)
        await page.wait_for_timeout(random.randint(50, 200))


def jittered_delay(base_min: float = 1.5, base_max: float = 4.0) -> float:
    """Return a delay value with Gaussian jitter, clamped to [base_min, base_max * 1.5]."""
    mean = (base_min + base_max) / 2
    std = (base_max - base_min) / 4
    delay = random.gauss(mean, std)
    return max(base_min, min(delay, base_max * 1.5))
