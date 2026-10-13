"""kindle.list_books — list Kindle books known to the plugin (disk + corpus)."""

from __future__ import annotations

import re

from research_engine_sdk import tool

from .._paths import EXTRACTED_DIR

# Loose ASIN shape: 10 alphanumeric chars, typically starting with B0.
_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


@tool(
    id="kindle.list_books",
    description=(
        "List Kindle books known to the plugin. Combines locally-scraped text files "
        "with corpus documents that look like Kindle ingests, joined on ASIN."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "include_ingested": {
                "type": "boolean",
                "default": True,
                "description": "If true, look up matching corpus documents.",
            },
        },
    },
)
async def handler(
    include_ingested: bool = True,
    ingestion=None,
    **_clients,
) -> dict:
    # Disk side
    disk_books: dict[str, dict] = {}
    if EXTRACTED_DIR.exists():
        for txt in sorted(EXTRACTED_DIR.glob("*.txt")):
            asin = txt.stem
            if not _ASIN_RE.match(asin):
                continue
            stat = txt.stat()
            disk_books[asin] = {
                "asin": asin,
                "on_disk": True,
                "disk_path": str(txt),
                "disk_size_bytes": stat.st_size,
                "disk_modified_at": stat.st_mtime,
                "ingested": False,
                "documents": [],
            }

    # Corpus side
    if include_ingested and ingestion is not None:
        try:
            for asin, entry in disk_books.items():
                docs = await ingestion.find_existing(source_pattern=asin)
                if docs:
                    entry["ingested"] = True
                    entry["documents"] = docs
        except Exception as exc:
            return {
                "books": list(disk_books.values()),
                "warning": f"Corpus lookup failed: {exc}",
            }

    books = sorted(disk_books.values(), key=lambda b: b["asin"])
    return {
        "count": len(books),
        "ingested_count": sum(1 for b in books if b["ingested"]),
        "books": books,
    }
