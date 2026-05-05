# Changelog

## 0.2.0 — 2026-05-05

**Breaking — requires `research-engine >= 0.2.0`.**

### Added
- `kindle.ingest_book` — scrape a book by ASIN (or reuse the on-disk cache) and
  ingest it into the corpus as `document_type='kindle_book'`. Idempotent: returns
  the existing `document_id` if the book is already in the corpus. `rescrape=true`
  forces a fresh KCR scrape; `force_reingest=true` ingests a duplicate.
- `kindle.check_book` — look up a book by ASIN. Reports both on-disk presence and
  any matching corpus documents.
- `kindle.list_books` — list every Kindle book the plugin knows about, joining
  the local extracted-text cache with corresponding corpus documents.

### Changed
- **Package directory renamed `code/` → `kindle/`** to avoid clashing with the
  Python stdlib `code` module. All imports and `pack.yaml` entries updated.
- **`kindle.scrape_book` is now actually callable.** The previous handler was
  written as `tool_handler(container, *, …)` but core dispatch invokes plugin
  tools as `_fn(**clients, **arguments)`, so it crashed on every call with
  `TypeError: missing argument 'container'`. The handler is now decorated with
  `@tool(...)` and accepts the injected SDK clients as kwargs, the same way
  acad and logos do it. The MCP input schema is now populated as well — it was
  empty before.
- `pack.yaml`: added `permissions.ingest: true` (required for the new ingest
  path), tightened `requires.core_api` to `">=0.2.0,<1.0.0"`.

### Storage
Books ingested via `kindle.ingest_book` land in the corpus as:

- `document_type='kindle_book'`
- `metadata`: `asin`, `kindle_title`, `scraped_at`, `page_count` (when
  available), `char_count`, `source_url`
- chunked with `prose_window`

### Migrating from 0.1.0

- Uninstall + reinstall: `re plugin uninstall kindle && re plugin install github:John-Cusack/marginalia-plugin-kindle@v0.2.0`.
- Books that were ingested via the generic `plain_text` path (i.e. by passing
  the extracted `.txt` file to `re ingest`) are present but typed as `generic`
  with no ASIN metadata. Either re-ingest them with `kindle.ingest_book(... force_reingest=true)`
  (creates duplicates), or run a one-shot SQL update to retype the existing
  rows in place.

## 0.1.0 — initial release

- `kindle.scrape_book` (broken — see 0.2.0 notes).
