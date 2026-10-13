"""Synthetic image round trips and scraper boundaries; no Amazon sessions."""

from __future__ import annotations

import io
from dataclasses import replace

import httpx
import pytest
from PIL import Image, ImageDraw

from kindle import ocr_server
from kindle.ocr_config import OcrSettings
from kindle.ocr_wire import PageImage
from kindle.scraper import ocr, reader
from kindle.scraper.ocr_remote import OcrBatchClient, remote_ocr_batch


def png(number: int, *, chrome: bool = False) -> bytes:
    image = Image.new("RGB", (240, 200), (number + 10, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.text((20, 90), f"Invented orchard page {number}", fill="black")
    if chrome:
        draw.rectangle((0, 0, 239, 59), fill="black")
        draw.rectangle((0, 140, 239, 199), fill="black")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


class PixelReader:
    device = "cuda:0"

    def readtext(self, pixels, **kwargs):
        return [f"Orchard page {int(pixels[0, 0, 0]) - 10}"]


@pytest.fixture
async def service(monkeypatch):
    monkeypatch.setattr(ocr_server, "_load_reader", lambda settings: PixelReader())
    app = ocr_server.create_app(OcrSettings())
    async with app.router.lifespan_context(app):
        yield httpx.ASGITransport(app=app)


async def test_numbered_png_batch_round_trips_in_manifest_order(service):
    pages = [PageImage(i, png(i)) for i in [9, 2, 7]]
    settings = OcrSettings(base_url="http://ocr.test")
    assert await remote_ocr_batch(pages, settings, transport=service) == [
        "Orchard page 9", "Orchard page 2", "Orchard page 7"
    ]


@pytest.mark.parametrize("failure", ["connection", "503", "missing-page", "wrong-version"])
async def test_remote_failure_falls_back_whole_batch_and_opens_circuit(monkeypatch, failure):
    monkeypatch.setattr(ocr, "_get_reader", lambda: PixelReader())
    settings = OcrSettings(base_url="http://ocr.test", retries=0)
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={
                "protocol": 1, "model": settings.model, "model_version": settings.version,
                "warm": True, "device": "cuda:0",
            })
        if failure == "connection":
            raise httpx.ConnectError("synthetic outage", request=request)
        if failure == "503":
            return httpx.Response(503)
        return httpx.Response(200, json={
            "protocol": 1, "model": settings.model,
            "model_version": "other-version" if failure == "wrong-version" else settings.version,
            "results": [{"page_id": 1, "text": "partial result must be discarded"}],
        })

    client = OcrBatchClient(settings, transport=httpx.MockTransport(handle))
    pages = [PageImage(i, png(i)) for i in [1, 3]]
    assert await client.recognize(pages) == ["Orchard page 1", "Orchard page 3"]
    assert client.remote_enabled is False
    calls_before = list(calls)
    assert await client.recognize([PageImage(4, png(4))]) == ["Orchard page 4"]
    assert calls == calls_before  # No repeated outage delays on subsequent pages.


@pytest.mark.parametrize("path", ["/health", "/v1/ocr"])
async def test_transient_retry_recovers_without_local_ocr(monkeypatch, service, path):
    settings = OcrSettings(base_url="http://ocr.test", retries=1)
    attempts = 0

    class FirstUnavailable(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            nonlocal attempts
            if request.url.path == path:
                attempts += 1
                if attempts == 1:
                    return httpx.Response(503)
            return await service.handle_async_request(request)

    def unavailable(*args, **kwargs):
        raise AssertionError("Successful retry must not use local OCR")

    monkeypatch.setattr(ocr, "ocr_image_bytes", unavailable)
    client = OcrBatchClient(settings, transport=FirstUnavailable())
    assert await client.recognize([PageImage(6, png(6))]) == ["Orchard page 6"]
    assert attempts == 2
    assert client.remote_enabled is True


@pytest.mark.parametrize("path", ["/health", "/v1/ocr"])
@pytest.mark.parametrize("invalid_protocol", [True, 1.0])
async def test_malformed_protocol_response_falls_back(monkeypatch, service, path, invalid_protocol):
    monkeypatch.setattr(ocr, "_get_reader", lambda: PixelReader())

    class MalformedProtocol(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            response = await service.handle_async_request(request)
            if request.url.path == path:
                await response.aread()
                body = response.json()
                body["protocol"] = invalid_protocol
                return httpx.Response(200, json=body)
            return response

    client = OcrBatchClient(OcrSettings(base_url="http://ocr.test"), transport=MalformedProtocol())
    assert await client.recognize([PageImage(2, png(2))]) == ["Orchard page 2"]
    assert client.remote_enabled is False


class ScrapePage:
    viewport_size = {"width": 240, "height": 200}

    def __init__(self, numbers):
        self.numbers = numbers
        self.index = 0
        self.keyboard = self
        self.closed = False
        self.captured = []

    async def goto(self, *args, **kwargs):
        pass

    async def wait_for_timeout(self, *args):
        pass

    async def press(self, key):
        self.index += 1

    async def screenshot(self, **kwargs):
        self.captured.append(self.index)
        return png(self.numbers[self.index], chrome=True)

    async def close(self):
        self.closed = True

    async def new_page(self):
        return self


async def prepare_scrape(monkeypatch, page):
    async def yes(*args, **kwargs):
        return True

    async def frame(*args):
        return page

    async def title(*args, **kwargs):
        return "Invented Orchard"

    async def no_op(*args, **kwargs):
        pass

    async def last(*args):
        return page.index == len(page.numbers) - 1

    monkeypatch.setattr(reader, "_is_kryptonite", yes)
    monkeypatch.setattr(reader, "_get_content_frame", frame)
    monkeypatch.setattr(reader, "_extract_title", title)
    monkeypatch.setattr(reader, "_navigate_to_start", no_op)
    monkeypatch.setattr(reader, "_is_last_page", last)
    monkeypatch.setattr(reader, "humanize_mouse", no_op)
    monkeypatch.setattr(ocr, "_get_reader", lambda: PixelReader())


@pytest.mark.parametrize("max_pages", [None, 3])
async def test_scraper_batches_and_flushes_final_partial_batch(monkeypatch, service, max_pages):
    page = ScrapePage([1, 2, 3, 4, 5])
    await prepare_scrape(monkeypatch, page)
    monkeypatch.setenv("KINDLE_OCR_BASE_URL", "http://ocr.test")
    monkeypatch.setenv("KINDLE_OCR_BATCH_SIZE", "2")
    monkeypatch.setenv("KINDLE_OCR_MANAGE_GPU", "never")
    batch_sizes = []

    class RecordingClient(OcrBatchClient):
        def __init__(self, settings):
            super().__init__(settings, transport=service)

        async def recognize(self, pages):
            batch_sizes.append(len(pages))
            return await super().recognize(pages)

    monkeypatch.setattr(reader, "OcrBatchClient", RecordingClient)
    result = await reader.scrape_book(page, "SYNTHETIC", 0, 0, max_pages=max_pages)
    count = max_pages or 5
    assert result["text"] == "\n\n".join(f"Orchard page {i}" for i in range(1, count + 1))
    assert result["page_count"] == count
    assert batch_sizes == ([2, 1] if max_pages else [2, 2, 1])
    assert page.captured == list(range(count))
    assert page.closed


@pytest.mark.parametrize("remote", [False, True])
async def test_no_progress_stops_and_deduplicates_in_both_modes(monkeypatch, service, remote):
    page = ScrapePage([1] * 20)
    await prepare_scrape(monkeypatch, page)
    monkeypatch.delenv("KINDLE_OCR_BASE_URL", raising=False)
    if remote:
        monkeypatch.setenv("KINDLE_OCR_BASE_URL", "http://ocr.test")
        monkeypatch.setenv("KINDLE_OCR_BATCH_SIZE", "4")
        monkeypatch.setenv("KINDLE_OCR_MANAGE_GPU", "never")
        monkeypatch.setattr(reader, "OcrBatchClient", lambda s: OcrBatchClient(s, transport=service))
    else:
        def forbidden(*args, **kwargs):
            raise AssertionError("Default local scrape must not control remote service")
        monkeypatch.setattr(reader, "ensure_ocr_host_ready", forbidden)
    result = await reader.scrape_book(page, "SYNTHETIC", 0, 0)
    assert result["text"] == "Orchard page 1"
    assert result["page_count"] == 5
    assert len(page.captured) == (8 if remote else 6)


async def test_start_failure_leaves_scrape_on_local_path(monkeypatch):
    page = ScrapePage([3, 4])
    await prepare_scrape(monkeypatch, page)
    monkeypatch.setenv("KINDLE_OCR_BASE_URL", "http://ocr.test")

    async def failed_start(settings):
        raise RuntimeError("synthetic control failure")

    monkeypatch.setattr(reader, "ensure_ocr_host_ready", failed_start)
    result = await reader.scrape_book(page, "SYNTHETIC", 0, 0)
    assert result["text"] == "Orchard page 3\n\nOrchard page 4"
    assert result["page_count"] == 2


@pytest.mark.parametrize("setting", [
    {"base_url": "ssh://host"}, {"ssh_target": "-oProxyCommand=bad"},
    {"batch_size": 17}, {"timeout": float("nan")}, {"model": "unknown"},
])
def test_invalid_settings_fail_before_network(setting):
    with pytest.raises(ValueError):
        replace(OcrSettings(), **setting)
