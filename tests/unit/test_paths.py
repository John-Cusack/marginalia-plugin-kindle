"""Tests for the shared path helpers (no core dependency)."""

from __future__ import annotations

from pathlib import Path

from kindle._paths import EXTRACTED_DIR, PLUGIN_DATA_DIR, text_path_for


def test_plugin_data_dir_under_marginalia():
    assert Path.home() / ".marginalia" / "plugins" / "kindle" == PLUGIN_DATA_DIR


def test_extracted_dir_under_plugin_data():
    assert EXTRACTED_DIR == PLUGIN_DATA_DIR / "extracted"


def test_text_path_for_uses_asin_filename():
    assert text_path_for("B003NX6Z3W") == EXTRACTED_DIR / "B003NX6Z3W.txt"


def test_text_path_for_does_not_validate_asin():
    # We accept whatever the caller passes; no validation here. ASIN shape is
    # enforced downstream (e.g. by Amazon when scraping).
    assert text_path_for("anything").name == "anything.txt"
