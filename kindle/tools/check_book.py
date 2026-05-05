"""kindle.check_book — report disk + corpus status for a Kindle book by ASIN."""

from __future__ import annotations

from research_engine.plugins.sdk import tool

from .._paths import text_path_for


@tool(
    id="kindle.check_book",
    description=(
        "Check whether a Kindle book is on disk (scraped) and/or already ingested into "
        "the corpus. Looks up by ASIN."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "book_asin": {"type": "string", "description": "The ASIN to check."},
        },
        "required": ["book_asin"],
    },
)
async def handler(
    book_asin: str,
    ingestion=None,
    **_clients,
) -> dict:
    text_path = text_path_for(book_asin)
    on_disk = text_path.exists()
    disk_info: dict | None = None
    if on_disk:
        stat = text_path.stat()
        disk_info = {
            "path": str(text_path),
            "size_bytes": stat.st_size,
            "modified_at": stat.st_mtime,
        }

    ingested: list[dict] = []
    if ingestion is not None:
        # Match on the ASIN appearing anywhere in the source path. Catches both
        # canonical ingests (.../extracted/{asin}.txt) and any out-of-band ones.
        try:
            ingested = await ingestion.find_existing(source_pattern=book_asin)
        except Exception as exc:
            ingested = []
            return {
                "asin": book_asin,
                "on_disk": on_disk,
                "disk": disk_info,
                "ingested": False,
                "documents": [],
                "warning": f"Corpus lookup failed: {exc}",
            }

    return {
        "asin": book_asin,
        "on_disk": on_disk,
        "disk": disk_info,
        "ingested": bool(ingested),
        "documents": ingested,
    }
