# marginalia-plugin-kindle

Kindle Cloud Reader scraper pack for [MarginaliaAI](https://github.com/MarginaliaAI). Extracts book text from [read.amazon.com](https://read.amazon.com) page-by-page using Playwright, with interactive Amazon login and cookie reuse.

## Prerequisites

- Python 3.11+
- A MarginaliaAI core installation (`>=0.1.0`)
- An Amazon account with Kindle books in your Cloud Reader library

## Install

```bash
re plugin install github:MarginaliaAI/marginalia-plugin-kindle
```

This will install the `playwright` pip dependency and run `playwright install chromium` automatically.

## Usage

The pack provides one MCP tool: **`kindle.scrape_book`**.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `book_asin` | `str` | *required* | The ASIN of the book to scrape |
| `force_reauth` | `bool` | `false` | Force a fresh Amazon login, ignoring cached cookies |
| `page_delay_min` | `float` | `1.5` | Minimum seconds between page turns |
| `page_delay_max` | `float` | `4.0` | Maximum seconds between page turns |
| `max_pages` | `int \| null` | `null` | Stop after N pages (null = scrape entire book) |

### Finding an ASIN

The ASIN is in the Kindle Cloud Reader URL when you open a book:

```
https://read.amazon.com/?asin=B0XXXXXXXX
                              ^^^^^^^^^^
```

Or find it on the book's Amazon product page URL.

### Example

```python
result = await tool("kindle.scrape_book", book_asin="B08N5WRWNW")
```

### First run

On first invocation (or with `force_reauth: true`) a **visible Chromium window** opens to `read.amazon.com`. Log in to your Amazon account manually — you have up to 5 minutes. Cookies are cached at `~/.marginalia/plugins/kindle/cookies.json` so subsequent runs skip login.

### Output

Extracted text is saved to `~/.marginalia/plugins/kindle/extracted/{asin}.txt` and the tool returns:

```json
{
  "status": "success",
  "title": "Book Title",
  "asin": "B08N5WRWNW",
  "page_count": 342,
  "text_length": 198402,
  "text_preview": "First 500 characters...",
  "output_path": "/home/you/.marginalia/plugins/kindle/extracted/B08N5WRWNW.txt"
}
```

## Limitations

- **Canvas-rendered books** (KF8 fixed-layout) render to `<canvas>` — text extraction won't work for these. Standard reflowable Kindle books work fine.
- **Amazon DOM changes** — KCR selectors shift over time. The scraper uses multiple fallback selectors but may need updates.
- **Rate limiting** — scraping too aggressively or too many books in a short period may trigger Amazon's bot detection. The default delays (1.5–4.0s) are conservative.
- **Non-headless only** — Amazon detects headless Chromium, so the browser window must be visible during scraping.

## How it works

1. Launches Chromium with anti-detection flags (no `navigator.webdriver`, realistic fingerprint)
2. Loads cached cookies or prompts for interactive Amazon login
3. Opens the book in Cloud Reader by ASIN
4. Navigates to the beginning, then turns pages with `ArrowRight`
5. Extracts text from the DOM on each page via JavaScript
6. Deduplicates, joins, and saves the full text

## License

Apache-2.0
