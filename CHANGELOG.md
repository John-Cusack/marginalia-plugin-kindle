# Changelog

## Unreleased

### Added
- Opt-in, in-memory numbered PNG batches for Kryptonite OCR on a remote CUDA
  host, with local default and whole-batch fallback. Final partial batches,
  page limits, deduplication, and no-progress termination retain page order.
- Dedicated Kindle-owned FastAPI service and user systemd unit, versioned
  EasyOCR 1.7.2/Craft/recognizer handshake, bounded uploads, serialized GPU
  inference, and responsive health checks. No core server change.
- Explicit `python -m kindle.ocr_control start|stop|status`: healthy and skipped
  paths perform no host control; scrapes and failures never stop a service.
- Environment-only endpoint, SSH target, model, batch, retry, and deadline
  configuration. Deployment and privacy guidance in README.

### Fixed
- Use the public `research_engine_sdk` tool decorator instead of the removed
  `research_engine.plugins.sdk` import, verified with published core 0.6.2.
- Serialize local reader initialization/inference when remote fallback runs
  in worker threads. Queued remote uploads wait before allocating a full body;
  canceled requests keep the GPU slot until their worker finishes.
- Deployment guidance uses direct Tailscale HTTP without a laptop tunnel,
  remote-only unit restarts, and explicit client dotenv loading across worktrees.

### Verification
- Synthetic CUDA HTTP OCR returned invented page markers in order. Warm,
  four-page 1280×780 synthetic batch: CPU 514.39 s, remote 0.963 s including
  transport (534.2×); browser delays and model warm-up excluded. This synthetic
  CPU workload was much slower than the established ~13 s/book-page observation;
  it is not a real-book speedup forecast. Full method and settings in README.
- Live laptop-to-GPU direct Tailscale OCR verified warm CUDA readiness and
  ordered recognition of two invented page markers.

## 0.3.1 — 2026-05-05

### Fixed
- **Kryptonite books now actually start at page 1.** In 0.3.0,
  `_navigate_to_start` only knew the classic-KCR controls (Home key, header
  menu's "Go to Beginning") — Kryptonite ignores both, so KCR served whatever
  page it remembered from the user's previous session. The first full-book
  scrape of *Shield of Achilles* missed the cover, dedication, table of
  contents, preface, and the first ~2% of the introduction.
  Fix: in Kryptonite mode, set `#kr-scrubber-bar.value = 0` and dispatch
  `ionChange` + `ionInput`. KCR repaginates to Location 1. Falls through to
  the classic path if the scrubber isn't present (defensive).

### Added
- 4 unit tests covering the navigate-to-start branching: scrubber happy path,
  scrubber-missing fallthrough, evaluate-throws fallthrough, and that
  classic-mode never touches the scrubber.

### Tests
- 35/35 unit pass (added 4).

## 0.3.0 — 2026-05-05

### Added
- **Kryptonite reader support.** Amazon's new in-browser KCR renderer
  ("Kryptonite") draws book content via WebGL and exposes no extractable text
  in the DOM. The scraper now detects Kryptonite by its
  `webpackManifest_Kryptonite*` window marker, waits the longer warm-up the
  new shell needs (~12s vs the classic 5s), and switches to a screenshot+OCR
  per-page extraction path. Header/footer chrome bands are cropped before OCR
  so the title bar and "Location N of M" indicator don't get duplicated on
  every page.
- **GPU-accelerated OCR via EasyOCR.** New `kindle.scraper.ocr` module owns
  a lazily-cached `easyocr.Reader(["en"], gpu=True)`. Reuses the user's
  existing CUDA stack (already present for BGE embeddings) so first-call
  cost is the model load (~3s) and ~64MB of one-time downloads. Steady-state
  OCR is ~0.3-1s per page on a consumer GPU.
- New dependency: `easyocr>=1.7` (declared in both `pack.yaml.requires.pip`
  and `pyproject.toml`).
- Title fallback for Kryptonite mode: when the classic title selectors miss,
  pull the first non-chrome line of `body.innerText` (skipping "Kindle
  Library", "Location N of M", and the "Learning reading speed..." status).

### Notes
- This release does not regress the classic-KCR path; books that aren't on
  Kryptonite still use the DOM extractor and the existing tesseract fallback
  for image-rendered pages.
- 31/31 unit tests pass (added 11: 4 OCR module, 7 reader Kryptonite
  routing).

## 0.2.1 — 2026-05-05

### Fixed
- **Empty-text scrapes no longer write a 0-byte cache file.** In 0.2.0, if a
  scrape produced no text (canvas-rendered KF8 book, captcha shown by Amazon,
  or DOM drift), `scrape_to_disk` still wrote an empty `.txt` to the extracted
  cache. A subsequent `kindle.ingest_book` call would see the file, skip
  scraping, and immediately return `error_type: empty_text` — a sticky failure
  with no diagnostic value. Now `scrape_to_disk` only writes when text is
  non-empty, so retries actually re-scrape.
- **Plugin progress is now visible.** Scraper modules used stdlib `logging`,
  but core configures `structlog` only — so every page-by-page log line was
  silently dropped. All plugin modules now use `structlog.get_logger` with
  event-style `(event, key=value)` calls, matching the convention in core /
  acad / logos.

### Added
- 3 regression tests for `_scrape.scrape_to_disk` covering the no-empty-write
  invariant.

### Tested
- 20/20 unit tests pass (was 17 in 0.2.0).
- Lint clean.

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
