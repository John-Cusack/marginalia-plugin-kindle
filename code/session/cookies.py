"""JSON cookie persistence for Kindle Cloud Reader sessions."""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class CookieStore:
    """Load and save browser cookies as JSON."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> list[dict]:
        """Load cookies from disk. Returns empty list on missing/corrupt file."""
        if not self.path.exists():
            log.debug("No cookie file at %s", self.path)
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            log.warning("Cookie file is not a list, ignoring: %s", self.path)
            return []
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to load cookies from %s: %s", self.path, exc)
            return []

    def save(self, cookies: list[dict]) -> None:
        """Persist cookies to disk as JSON."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(cookies, indent=2, default=str),
            encoding="utf-8",
        )
        log.debug("Saved %d cookies to %s", len(cookies), self.path)

    def clear(self) -> None:
        """Delete the cookie file if it exists."""
        if self.path.exists():
            self.path.unlink()
            log.debug("Cleared cookies at %s", self.path)
