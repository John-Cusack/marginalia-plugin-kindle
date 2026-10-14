# marginalia-plugin-kindle

Kindle Cloud Reader pack for [MarginaliaAI](https://github.com/MarginaliaAI). Scrapes book text from [read.amazon.com](https://read.amazon.com) page-by-page using Playwright (interactive Amazon login + cookie reuse), and ingests it into the corpus as `kindle_book` documents.

## Prerequisites

- Python 3.11+
- A compatible MarginaliaAI core installation, `>=0.6,<0.7` (tested with published `0.6.2` and workspace `0.6.3`; tools use the public `research_engine_sdk`)
- An Amazon account with the books in your Kindle Cloud Reader library

## Install

Kindle is not published on PyPI. Core's not-installed hint names
`marginalia-ai-plugin-kindle`, but that package does not exist. Build a wheel
from this checkout instead. Install it into the **same Python environment that
runs `research-engine serve`**. Core never installs plugin code, and a plugin
in another virtual environment is invisible to it.

```bash
uv build --wheel --out-dir dist
uv pip install --python /path/to/core/.venv/bin/python \
  dist/marginalia_plugin_kindle-0.4.0-py3-none-any.whl
playwright install chromium   # Playwright wheels ship no browser
```

The wheel advertises one `research_engine.plugins` entry point (`kindle =
kindle`) and carries its schema-v2 manifest at `kindle/plugin.yaml`. Core
reads that manifest without importing Kindle code. Approve the exact installed
artifact, then restart the MCP server:

```bash
research-engine plugin audit kindle    # permissions, tools, manifest hash
research-engine plugin enable kindle
```

Run these from the core checkout whose `.env` names the database the MCP
server uses: approvals live in that database. A row left by the pre-0.4
`plugin install` flow audits as `legacy`, and `plugin enable kindle` replaces
it. After reinstalling a changed version or manifest, use
`research-engine plugin approve-upgrade kindle`; the loader skips a plugin
whose installed version or manifest hash differs from its approval. Kindle
declares no database, so there is nothing to migrate.

In a uv-managed core workspace, `uv run` leaves this out-of-lock install in
place, but an exact `uv sync` removes it. Reinstall afterwards.

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

- **Canvas/Kryptonite rendering** — DOM extraction cannot read these pixels. Kryptonite uses screenshot + EasyOCR (local by default, optional remote CUDA); classic image pages retain the `pytesseract` fallback.
- **Amazon DOM changes** — KCR selectors shift over time. The scraper uses fallback selectors but may need updates.
- **Rate limiting** — scraping aggressively or many books in quick succession may trigger Amazon's bot detection. Default delays (1.5–4.0s/page) are conservative.
- **Non-headless only** — Amazon detects headless Chromium, so the browser window must be visible during scraping.

## How it works

1. Launches Chromium with anti-detection flags (no `navigator.webdriver`, realistic fingerprint).
2. Loads cached cookies or prompts for interactive Amazon login.
3. Opens the book in Cloud Reader by ASIN.
4. Navigates to the beginning, then turns pages with `ArrowRight`.
5. Extracts DOM text; Kryptonite captures cropped screenshots, batching them only when remote OCR is configured.
6. Deduplicates, joins, saves the full text.
7. (`kindle.ingest_book` only) Chunks with `prose_window`, embeds, and stores via the core ingestion pipeline.

## Optional GPU OCR offload

Local OCR remains the default: with `KINDLE_OCR_BASE_URL` unset, scraping opens
no OCR network connection and invokes no remote service control. Remote failures
discard the entire batch result, OCR the retained images locally in order, and
disable remote attempts for the remainder of that scrape. Browser page-turn
delays are unchanged. Partial batches flush at the last page or `max_pages`;
deduplication and the five-page no-progress cutoff still apply. A remote batch
can capture up to `batch_size - 1` speculative pages beyond that cutoff; their
results are discarded.

### Placement

The Kindle-owned `kindle.ocr_server` and `marginalia-kindle-ocr.service` keep
EasyOCR dependencies, model warm-up, deployment, and release cadence independent
of core's BGE embedding/rerank server. **No core diff is required.** Both follow
the same pattern: warm models, one GPU semaphore, blocking inference off the
event loop, responsive `/health`, and SSH-controlled systemd user units.

The separate semaphore does **not** coordinate GPU occupancy with the embed
server. Schedule large embedding/rerank and OCR workloads separately on the
same card; this change never starts, stops, or reconfigures the embed unit.
Adding OCR to core instead would share its semaphore, but requires a second
repository change and couples Kindle startup to embedding warm-up.

### Server installation

On the GPU host, in an isolated environment (CUDA PyTorch must match its driver):

```bash
python3 -m venv ~/.local/share/marginalia-kindle-ocr/venv
~/.local/share/marginalia-kindle-ocr/venv/bin/python -m pip install \
  torch torchvision --index-url https://download.pytorch.org/whl/cu128
```

Build and transfer the plugin wheel and unit from this checkout; `OCR_SSH_TARGET`
is an operator-defined SSH host alias or `user@host`, not an Amazon credential:

```bash
uv build --wheel
scp dist/marginalia_plugin_kindle-*.whl \
  "$OCR_SSH_TARGET":.local/share/marginalia-kindle-ocr/
ssh "$OCR_SSH_TARGET" 'mkdir -p ~/.config/systemd/user'
scp deploy/marginalia-kindle-ocr.service \
  "$OCR_SSH_TARGET":.config/systemd/user/
```

On the GPU host, install the transferred wheel, then reload the unit:

```bash
~/.local/share/marginalia-kindle-ocr/venv/bin/python -m pip install \
  "$HOME/.local/share/marginalia-kindle-ocr/marginalia_plugin_kindle-0.3.1-py3-none-any.whl[ocr-server]"
systemctl --user daemon-reload
```

The first start downloads EasyOCR's built-in model weights. CUDA is mandatory:
the service refuses CPU fallback and requires pinned EasyOCR 1.7.2. The unit
is installed but not enabled automatically. It stays running until an explicit
stop; no idle shutdown, scrape-exit stop, or failure-triggered stop is issued.
The host's user systemd manager must remain available; enabling user lingering,
if required by its login policy, is an operator/admin decision.

Default bind is `127.0.0.1:9883`. When both machines use Tailscale, prefer
**direct HTTP over Tailscale**, like the embedding service: no laptop tunnel
or foreground process is needed. SSH is used only for unit control.

On the GPU host, set `KINDLE_OCR_BIND_HOST` to its Tailscale IPv4 address
(reported by `tailscale ip -4`) in `~/.config/marginalia-kindle-ocr.env`.
Keep `KINDLE_OCR_PORT=9883`. To apply a bind change, restart only the OCR unit
when no scrape is using it. From the laptop:

```bash
ssh "$OCR_SSH_TARGET" 'systemctl --user restart marginalia-kindle-ocr.service'
```

The unit exists on the GPU host, not the laptop; running bare `systemctl --user`
on the laptop controls the wrong machine. Set `OCR_TAILSCALE_IP` to the GPU
host's actual address, then configure the laptop process:

```bash
export KINDLE_OCR_BASE_URL="http://${OCR_TAILSCALE_IP}:9883"
export KINDLE_OCR_SSH_TARGET="$OCR_SSH_TARGET"
```

Allow the scraper to reach port 9883 through the tailnet ACL/host firewall.
Bind only to the Tailscale address, not `0.0.0.0`: the service is unauthenticated
and **must never be exposed to an untrusted network**.

If direct Tailscale access is unsuitable, keep the loopback bind and use an
optional SSH forward instead:

```bash
ssh -N -o ExitOnForwardFailure=yes \
  -L 18883:127.0.0.1:9883 "$OCR_SSH_TARGET"
export KINDLE_OCR_BASE_URL=http://127.0.0.1:18883
```

The plugin's network permission allows egress because the OCR host is chosen
at runtime; the operator controls the destination. HTTP clients ignore
environment proxies, send no Amazon credentials, and do not follow redirects.

### Control

From this checkout, set `OCR_CLIENT_ENV_FILE` to the **absolute path** of the
saved laptop `.env` containing the OCR settings, then run:

```bash
uv run --env-file "$OCR_CLIENT_ENV_FILE" --no-sync \
  python -m kindle.ocr_control status
```

Use `start` or `stop` in place of `status`. Kindle reads process environment
variables; it does not load `.env` itself. Core's settings loader does not
export `.env` into the environment either, so the MCP launcher must load the
saved file. Different worktrees may have different `.env` copies: do not
assume `$PWD/.env` is the file you edited. For a stdio MCP server started with
`uv run`, layer the saved file under the checkout's own `.env`:

```json
{
  "type": "stdio",
  "command": "uv",
  "args": ["run", "--env-file", "/absolute/path/to/saved.env",
           "--env-file", ".env", "research-engine", "serve"]
}
```

Later files win, and variables already in the launcher's environment win over
both. Keeping the checkout's `.env` last keeps its own `RE_DB_URL`, so loading
OCR settings cannot move the server to another database. The relative `.env`
resolves against the server's working directory. With Claude Code, register
this as a `local`-scope server of the same name, which overrides a project
`.mcp.json` without editing it. Restart (reconnect) the server afterwards.

`start` (also used before a remote scrape) reports `started`,
`already-running`, or `skipped`. A healthy, compatible CUDA endpoint returns
`already-running` without any SSH/systemctl call. Without an endpoint, or with
automatic management disabled, `start` reports `skipped` without any probe.
Otherwise it checks the dedicated unit, starts only an inactive unit, and
waits for a compatible warm-CUDA health response. SSH errors, missing units,
model mismatches, start failures, and startup deadlines raise immediately or
at the bounded deadline. The CLI exits nonzero; the scraper falls back locally.
No path implicitly stops a service, including startup timeout.

`status` reports `already-running`, `warming`, `inactive`, or `skipped`;
it never mutates the unit. Explicit `stop` reports `stopped`/`skipped` and
intentionally works even with `KINDLE_OCR_MANAGE_GPU=never`.

`status=skipped` means no endpoint was loaded; `warming` means a compatible
endpoint is not yet warm, or the unit is active but HTTP health is unreachable.
Neither result proves that remote OCR is usable. A running loopback-only unit
cannot be reached through a direct Tailscale endpoint.

### Configuration

Set these in the scraper process environment. No endpoint, SSH host, or secret
is stored in the repository.

| Variable | Default | Meaning |
| --- | --- | --- |
| `KINDLE_OCR_BASE_URL` | unset | Opt into the remote HTTP(S) endpoint |
| `KINDLE_OCR_SSH_TARGET` | endpoint hostname | Explicit SSH target; overrides local/remote hostname inference |
| `KINDLE_OCR_MODEL` | `english_g2` | Built-in recognizer: `english_g2` or `latin_g2`, English pages; set identically on server |
| `KINDLE_OCR_BATCH_SIZE` | `8` | Pages per request, range 1–16 |
| `KINDLE_OCR_TIMEOUT` | `120` | HTTP operation timeout in seconds; connect capped at 10 s |
| `KINDLE_OCR_RETRIES` | `1` | Additional transient attempts, range 0–2 |
| `KINDLE_OCR_MANAGE_GPU` | `auto` | `auto` starts on demand; `never` disables automatic control, not remote OCR |
| `KINDLE_OCR_START_TIMEOUT` | `180` | Warm-up deadline in seconds |
| `KINDLE_OCR_BIND_HOST` | `127.0.0.1` | Server-only listen interface |
| `KINDLE_OCR_PORT` | `9883` | Server-only listen port |

If an SSH tunnel/proxy changes the endpoint hostname, set the SSH target
explicitly so `systemctl --user` runs on the intended GPU host.

### Wire protocol and privacy

`POST /v1/ocr`, `Content-Type: application/zip`, sends a **ZIP_STORED** archive
entirely in memory. `manifest.json` contains `protocol: 1`, `model`,
`model_version`, and an ordered list of unique nonnegative integer page IDs.
Each ID names one PNG as `{page_id:09d}.png`; e.g. `000000002.png`.
Manifest order, not numeric filename sorting, is authoritative.

The JSON response repeats the protocol/model fields and returns
`results: [{"page_id": 2, "text": "..."}]`. Clients reconstruct input order by
ID and reject missing, extra, duplicate, or malformed results. Protocol/model
mismatches are never retried. `model_version` is
`easyocr-1.7.2:craft:<recognizer>`; the package pin plus EasyOCR's built-in
weight MD5 validation identifies the detector/recognizer. A model change
requires matching client and server configuration.

Limits: 16 PNGs, 32 MiB including manifest, 4 million decoded pixels per page,
single-frame PNG only. Upload admission happens before body buffering, so
queued batches cannot each retain a full 32 MiB body. `/health` bypasses
inference admission. Disconnecting a requester does not release the GPU slot
until its worker finishes.

Health and OCR requests retry only transport errors or HTTP 502/503/504,
with bounded 0.25/0.5 s backoff. Retried stateless requests may repeat GPU work
after a lost response; they cannot duplicate the returned book text. Other HTTP
errors, malformed responses, and model/version mismatches fall back immediately.
Timeouts apply per HTTP operation/attempt, not to the whole scrape.

An SCP page spool would need a unique `batch-id/` directory containing a manifest
and numbered PNGs, a returned `result.json`, and coordinated cleanup on both
hosts. HTTP avoids that disk retention and transfer/cleanup race. **SCP is used
only for deploying code/unit files, never book pages or Amazon cookies.**
The server never writes images or recognized text to disk and never logs
request bodies, OCR results, or backend exception contents. Do not add Amazon
cookies or licensed text to fixtures; OCR tests use invented synthetic pages.

### Synthetic benchmark

Measured on 2026-10-13 using four invented 1280×780 RGB PNG pages (321,436 bytes
total), transferred through SSH loopback forwarding to an RTX 3090. Each page
used DejaVu Sans 28 px: heading `ORCHARD PAGE ALPHA` (then BRAVO, CHARLIE, DELTA)
at (45, 35), and 12 lines at (45, 95 + 45 × line) reading
`Invented orchard row {line}: blue lanterns guide the quiet robots.`

Both paths used EasyOCR 1.7.2/Craft/english_g2 with paragraph output. Local CPU:
Python 3.13.11, Torch 2.11.0+cu130, default 14 CPU threads and unchanged local
recognition batching. Server: CUDA Torch 2.11.0+cu128, recognition batch size 32,
one page decoded at a time. The heading markers were checked in order for
both paths. One preload/first-page pass per path preceded the timed batch.

| Warm path | Four-page batch (s) | Seconds/page |
| --- | ---: | ---: |
| Local CPU | 514.388 | 128.597 |
| Remote RTX 3090, including health/PNG transfer/result return | 0.963 | 0.241 |

Measured OCR-only speedup: **534.2×**. Excluded CPU initialization/first-page
warm-up (286.575 s), remote first-page warm-up request (0.847 s), service
startup, downloads, and browser page-turn delays. This synthetic CPU batch
was much slower than the established ~13 s/real-book-page observation; it is
not a forecast for real licensed books, and OCR speedup is not scrape speedup.
No real Amazon authentication or book text was used.

### Development checks

```bash
uv sync --extra dev --extra ocr-server
uv run pytest tests/unit -q
uv run pytest -q
uv run ruff check kindle tests
```

The dev extra includes published core so the book-tool tests run too.
`tests/integration/test_built_wheel.py` builds this checkout with `uv build`
and installs the wheel into an empty target with `uv pip install`. Core's own
discovery, activation, loader, and MCP list/call handlers then run against
that install in a fresh interpreter, ending in a `kindle.check_book` call for
an invented ASIN. It needs `uv` on `PATH`. At this change: **97 unit tests
passed**, the full suite passed **103/103** with no skips, and Ruff was clean.
Without core installed, the core-dependent modules keep their optional skip
behavior.

## License

Apache-2.0
