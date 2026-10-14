"""Approve, load and call an installed Kindle wheel through core, in a fresh interpreter.

Usage: python -I wheel_probe.py SITE DATA_DIR ASIN

The test process already imports `kindle` from the checkout, so the wheel's
copy can only be proven to load in a separate interpreter whose `sys.path`
starts at the install target. Prints one JSON report on stdout.
"""

from __future__ import annotations

import asyncio
import json
import sys
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

SITE, DATA_DIR, ASIN = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, SITE)

from research_engine.mcp.dispatch import register_core_tools  # noqa: E402
from research_engine.plugins.activation import PluginActivationManager  # noqa: E402
from research_engine.plugins.discovery import scan_plugins  # noqa: E402
from research_engine.plugins.loader import PluginLoader  # noqa: E402
from research_engine.plugins.registry import PluginRegistry  # noqa: E402


class MemoryActivations:
    def __init__(self) -> None:
        self.row = None

    async def save(self, activation):
        self.row = activation

    async def get(self, plugin_id):
        return self.row if self.row and self.row.plugin_id == plugin_id else None

    async def list_all(self):
        return [self.row] if self.row else []

    async def list_enabled(self):
        return [self.row] if self.row and self.row.enabled else []


class RecordingIngestion:
    """The corpus side of check_book: records lookups, holds no documents."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def find_existing(self, *, source=None, source_pattern=None):
        self.calls.append({"source": source, "source_pattern": source_pattern})
        return []


class StubServer:
    """Captures the list/call handlers core gives the MCP server."""

    def list_tools(self):
        def deco(fn):
            self.list_fn = fn
            return fn

        return deco

    def call_tool(self):
        def deco(fn):
            self.call_fn = fn
            return fn

        return deco


async def main() -> dict:
    entry_points = [
        ep
        for dist in metadata.distributions(path=[SITE])
        for ep in dist.entry_points
        if ep.group == "research_engine.plugins"
    ]
    report = scan_plugins(entry_points)
    imported_by_discovery = "kindle" in sys.modules

    activations = MemoryActivations()
    await PluginActivationManager(activations).approve(
        "kindle", non_interactive=True, discovered_plugins=report.plugins
    )
    registry = PluginRegistry()
    registry.register_core_types()
    ingestion = RecordingIngestion()
    loader = PluginLoader(activations, registry, DATA_DIR / "plugin-data", ingestion=ingestion)
    loaded = await loader.load_enabled(report.plugins)

    server = StubServer()
    register_core_tools(server, SimpleNamespace(registry=registry, plugin_loader=loader))
    listed = {
        tool.name: {"description": tool.description, "input_schema": tool.inputSchema}
        for tool in await server.list_fn()
        if tool.name.startswith("kindle.")
    }
    [checked] = await server.call_fn("kindle.check_book", {"book_asin": ASIN})
    [rejected] = await server.call_fn("kindle.check_book", {})

    return {
        "issues": [str(issue) for issue in report.issues],
        "imported_by_discovery": imported_by_discovery,
        "loaded": loaded,
        "kindle_file": sys.modules["kindle"].__file__,
        "document_type": registry.list_document_types().get("kindle_book"),
        "listed": listed,
        "check_book": json.loads(checked["text"]),
        "check_book_without_asin": json.loads(rejected["text"]),
        "ingestion_calls": ingestion.calls,
    }


print(json.dumps(asyncio.run(main()), default=str))
