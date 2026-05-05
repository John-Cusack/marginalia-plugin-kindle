"""Shared filesystem paths for the Kindle plugin."""

from __future__ import annotations

from pathlib import Path

PLUGIN_DATA_DIR = Path.home() / ".marginalia" / "plugins" / "kindle"
COOKIE_PATH = PLUGIN_DATA_DIR / "cookies.json"
EXTRACTED_DIR = PLUGIN_DATA_DIR / "extracted"


def text_path_for(asin: str) -> Path:
    """Canonical on-disk path for a scraped book's text."""
    return EXTRACTED_DIR / f"{asin}.txt"
