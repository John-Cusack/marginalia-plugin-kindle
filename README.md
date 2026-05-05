# marginalia-plugin-kindle

Kindle Cloud Reader pack for [MarginaliaAI](https://github.com/MarginaliaAI). Scrapes book text from [read.amazon.com](https://read.amazon.com) page-by-page using Playwright (interactive Amazon login + cookie reuse), and ingests it into the corpus as `kindle_book` documents.

## Prerequisites

- Python 3.11+
- A MarginaliaAI core installation (`>=0.1.0`)
- An Amazon account with the books in your Kindle Cloud Reader library

## Install

```bash
re plugin install github:MarginaliaAI/marginalia-plugin-kindle
```

This installs Playwright and runs `playwright install chromium`.

## MCP tools

| Tool | What it does |
| --- | --- |
| `kindle.scrape_book` | Scrape a book by ASIN and write the text to `~/.marginalia/plugins/kindle/extracted/{asin}.txt`. Does **not** ingest. |
| `kindle.ingest_book` | Scrape (or reuse the on-disk cache) **and** ingest into the corpus as `document_type='kindle_book'`. Idempotent: returns the existing `document_id` if the book is already ingested. |
| `kindle.check_book` | Look up a book by ASIN — reports whether it's on disk and/or already in the corpus. |
| `kindle.list_books` | List Kindle books known to the plugin: locally-scraped texts joined with their corpus documents (if any). |

### Finding an ASIN

The ASIN is in the Kindle Cloud Reader URL when you open a book:

```
https://read.amazon.com/?asin=B0XXXXXXXX
                              ^^^^^^^^^^
```

### Typical workflow

```
# Is this book already there?
kindle.check_book(book_asin="B003NX6Z3W")

# Not there → scrape + ingest in one shot.
kindle.ingest_book(book_asin="B003NX6Z3W")

# What do I have?
kindle.list_books()
```

`kindle.ingest_book` is safe to call repeatedly — if the document is already in the corpus it short-circuits without re-scraping or re-embedding. Pass `rescrape=true` to force a fresh scrape, or `force_reingest=true` to ingest a second copy after a rescrape.

### First run

On the first invocation a **visible Chromium window** opens to `read.amazon.com`. Log in to your Amazon account manually — you have up to 5 minutes. Cookies are cached at `~/.marginalia/plugins/kindle/cookies.json` so subsequent runs skip login. Pass `force_reauth=true` to clear and re-prompt.

### Storage

- Scraped text → `~/.marginalia/plugins/kindle/extracted/{asin}.txt`
- Cookies → `~/.marginalia/plugins/kindle/cookies.json`
- Corpus document → `document_type='kindle_book'`, with metadata `{asin, kindle_title, scraped_at, page_count, char_count, source_url}`

## Limitations

- **Canvas-rendered books** (KF8 fixed-layout) render to `<canvas>` — text extraction via DOM won't work. Standard reflowable Kindle books work fine; image-rendered pages fall back to OCR via `pytesseract`.
- **Amazon DOM changes** — KCR selectors shift over time. The scraper uses fallback selectors but may need updates.
- **Rate limiting** — scraping aggressively or many books in quick succession may trigger Amazon's bot detection. Default delays (1.5–4.0s/page) are conservative.
- **Non-headless only** — Amazon detects headless Chromium, so the browser window must be visible during scraping.

## How it works

1. Launches Chromium with anti-detection flags (no `navigator.webdriver`, realistic fingerprint).
2. Loads cached cookies or prompts for interactive Amazon login.
3. Opens the book in Cloud Reader by ASIN.
4. Navigates to the beginning, then turns pages with `ArrowRight`.
5. Extracts text from the DOM on each page; falls back to OCR for image pages.
6. Deduplicates, joins, saves the full text.
7. (`kindle.ingest_book` only) Chunks with `prose_window`, embeds, and stores via the core ingestion pipeline.

## License

Apache-2.0
