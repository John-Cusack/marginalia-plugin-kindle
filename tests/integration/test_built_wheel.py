"""The built wheel, installed by a real installer, through core's own plugin machinery.

Builds this checkout with `uv build`, installs the wheel into an empty target
with `uv pip install`, and hands only that target's entry points to core.
Nothing here reads the source manifest: if the wheel lacks the entry point or
`kindle/plugin.yaml`, discovery finds nothing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import pytest

pytest.importorskip("research_engine")

from research_engine.plugins.discovery import scan_plugins  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PROBE = Path(__file__).with_name("wheel_probe.py")
TOOL_IDS = {"kindle.scrape_book", "kindle.ingest_book", "kindle.check_book", "kindle.list_books"}
# Invented: not a real book.
ASIN = "B0ZZINVENT"


def _uv(*args: str) -> None:
    uv = shutil.which("uv")
    assert uv, "uv is required to build and install the wheel under test"
    subprocess.run([uv, *args], cwd=REPO, check=True, capture_output=True, text=True)


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("dist")
    _uv("build", "--wheel", "--out-dir", str(out))
    [wheel] = out.glob("*.whl")
    target = tmp_path_factory.mktemp("site")
    _uv("pip", "install", "--quiet", "--no-deps", "--target", str(target), str(wheel))
    return target


@pytest.fixture(scope="module")
def discovered(site):
    entry_points = [
        ep
        for dist in metadata.distributions(path=[str(site)])
        for ep in dist.entry_points
        if ep.group == "research_engine.plugins"
    ]
    return scan_plugins(entry_points)


@pytest.fixture(scope="module")
def probe(site, tmp_path_factory) -> dict:
    home = tmp_path_factory.mktemp("home")
    extracted = home / ".marginalia" / "plugins" / "kindle" / "extracted"
    extracted.mkdir(parents=True)
    (extracted / f"{ASIN}.txt").write_text("Invented page one.\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", str(PROBE), str(site), str(home), ASIN],
        capture_output=True,
        text=True,
        check=False,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.splitlines()[-1])


def test_core_discovers_the_installed_wheel(site, discovered):
    assert discovered.issues == ()
    [plugin] = discovered.plugins

    assert plugin.plugin_id == "kindle"
    assert plugin.entry_point_name == "kindle"
    assert plugin.module_name == "kindle"
    assert plugin.distribution_name == "marginalia-plugin-kindle"
    assert plugin.manifest_path == (site / "kindle" / "plugin.yaml").resolve()
    assert {tool.id for tool in plugin.manifest.provides.mcp_tools} == TOOL_IDS
    assert [(t.id, t.default_chunker) for t in plugin.manifest.provides.document_types] == [
        ("kindle_book", "prose_window")
    ]
    assert plugin.manifest.provides.database is None


def test_manifest_requests_the_permissions_the_tools_use(discovered):
    [plugin] = discovered.plugins

    assert plugin.manifest.permissions.model_dump(mode="json") == {
        "network": "egress",
        "network_allowlist": [],
        "filesystem": "read_write_plugin_data",
        "llm": False,
        "ingest": True,
        "write": False,
        "subprocess": True,
    }


def test_approved_wheel_loads_from_the_install_target(site, probe):
    assert probe["issues"] == []
    assert probe["imported_by_discovery"] is False
    assert probe["loaded"] == ["kindle"]
    assert Path(probe["kindle_file"]).is_relative_to(site)
    assert probe["document_type"]["default_chunker"] == "prose_window"
    assert probe["document_type"]["plugin"] == "kindle"


def test_listed_tools_are_exactly_what_the_operator_approved(discovered, probe):
    """Core serves decorator metadata; the manifest is what gets approved."""
    [plugin] = discovered.plugins
    approved = {
        tool.id: {"description": tool.description, "input_schema": tool.input_schema}
        for tool in plugin.manifest.provides.mcp_tools
    }

    assert probe["listed"] == approved


def test_check_book_is_callable_through_the_mcp_handler(probe):
    result = probe["check_book"]

    assert result["asin"] == ASIN
    assert result["on_disk"] is True
    assert result["disk"]["size_bytes"] == len("Invented page one.\n")
    assert result["ingested"] is False
    # The manifest's ingest grant hands the handler core's ingestion service.
    assert probe["ingestion_calls"] == [{"source": None, "source_pattern": ASIN}]
    assert "warning" not in result


def test_check_book_input_is_validated_against_the_served_schema(probe):
    error = probe["check_book_without_asin"]["error"]

    assert error["code"] == "validation_error"
    assert "book_asin" in error["message"]
