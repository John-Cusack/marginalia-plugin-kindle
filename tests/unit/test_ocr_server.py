"""ASGI behavior with invented PNGs and a blocking, CUDA-free test backend."""

from __future__ import annotations

import asyncio
import io
import json
import struct
import sys
import threading
import zipfile
import zlib
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")
pytest.importorskip("numpy")

import httpx
from PIL import Image

from kindle import ocr_server
from kindle.ocr_config import MAX_BYTES, OcrSettings
from kindle.ocr_wire import PROTOCOL, PageImage, encode_batch, health_ready

SETTINGS = OcrSettings()
INVENTED_TEXT = {
    11: ["An invented amber paragraph.", "Another invented amber line."],
    22: ["An invented blue paragraph."],
    33: ["An invented green paragraph."],
}


def _image_bytes(color: int = 11, *, image_format: str = "PNG") -> bytes:
    with Image.new("RGB", (7, 5), (color, 0, 0)) as image:
        buf = io.BytesIO()
        image.save(buf, format=image_format)
        return buf.getvalue()


def _archive(
    pages: list[PageImage],
    *,
    overrides: dict | None = None,
    compression: int = zipfile.ZIP_STORED,
    extra_member: str | None = None,
) -> bytes:
    manifest = {
        "protocol": PROTOCOL,
        "model": SETTINGS.model,
        "model_version": SETTINGS.version,
        "pages": [page.page_id for page in pages],
        **(overrides or {}),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compression) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        # Physical archive order intentionally differs from manifest order.
        for page in reversed(pages):
            archive.writestr(f"{page.page_id:09d}.png", page.png)
        if extra_member is not None:
            archive.writestr(extra_member, b"invented unwanted bytes")
    return buf.getvalue()


def _invalid_zlib_png() -> bytes:
    png = _image_bytes()
    offset = 8
    while offset < len(png):
        length = struct.unpack_from(">I", png, offset)[0]
        kind = png[offset + 4:offset + 8]
        end = offset + 12 + length
        if kind == b"IDAT":
            data = b"invented invalid zlib stream"
            chunk = struct.pack(">I", len(data)) + kind + data
            chunk += struct.pack(">I", zlib.crc32(kind + data))
            return png[:offset] + chunk + png[end:]
        offset = end
    raise AssertionError("The invented PNG must contain IDAT")


def _oversized_png() -> bytes:
    png = _image_bytes()
    # A valid-CRC header whose area exceeds the cap, without allocating pixels.
    header = struct.pack(">II", 2_000_001, 2) + png[24:29]
    return png[:16] + header + struct.pack(">I", zlib.crc32(b"IHDR" + header)) + png[33:]


def _animated_png() -> bytes:
    buf = io.BytesIO()
    with (
        Image.new("RGB", (7, 5), (11, 0, 0)) as first,
        Image.new("RGB", (7, 5), (22, 0, 0)) as second,
    ):
        first.save(buf, format="PNG", save_all=True, append_images=[second], duration=100)
    return buf.getvalue()


class _InventedReader:
    device = "cuda"

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.second_entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.lock = threading.Lock()
        self.calls: list[dict] = []
        self.active = 0
        self.max_active = 0
        self.fail = False

    def readtext(self, pixels, **options):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append({
                "shape": pixels.shape,
                "color": int(pixels[0, 0, 0]),
                "thread": threading.get_ident(),
                **options,
            })
            self.entered.set()
            if len(self.calls) > 1:
                self.second_entered.set()
        try:
            if not self.release.wait(10):
                raise RuntimeError("Invented backend test timed out")
            if self.fail:
                raise RuntimeError("INVENTED_PRIVATE_BACKEND_DETAIL")
            return INVENTED_TEXT[int(pixels[0, 0, 0])]
        finally:
            with self.lock:
                self.active -= 1


@pytest.fixture
def backend(monkeypatch):
    reader = _InventedReader()
    loads = []

    def load(settings):
        loads.append((settings, threading.get_ident()))
        return reader

    monkeypatch.setattr(ocr_server, "_load_reader", load)
    return SimpleNamespace(reader=reader, loads=loads)


@asynccontextmanager
async def _client(app):
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://ocr.invalid"
        ) as client,
    ):
        yield client


async def _post(client, payload):
    return await client.post("/v1/ocr", content=payload, headers={"content-type": "application/zip"})


@pytest.mark.asyncio
async def test_manifest_order_and_page_ids_are_preserved(backend):
    pages = [
        PageImage(31, _image_bytes(11)),
        PageImage(4, _image_bytes(22)),
        PageImage(19, _image_bytes(33)),
    ]
    app = ocr_server.create_app(SETTINGS)
    async with _client(app) as client:
        response = await _post(client, _archive(pages))
        assert response.status_code == 200
        assert response.json() == {
            "protocol": PROTOCOL,
            "model": SETTINGS.model,
            "model_version": SETTINGS.version,
            "results": [
                {"page_id": 31, "text": "\n".join(INVENTED_TEXT[11])},
                {"page_id": 4, "text": "\n".join(INVENTED_TEXT[22])},
                {"page_id": 19, "text": "\n".join(INVENTED_TEXT[33])},
            ],
        }
        assert health_ready((await client.get("/health")).json(), SETTINGS)
    assert [call["color"] for call in backend.reader.calls] == [11, 22, 33]
    assert all(call["shape"] == (5, 7, 3) for call in backend.reader.calls)
    assert all(call["detail"] == 0 and call["paragraph"] is True for call in backend.reader.calls)
    assert all(call["batch_size"] == 32 for call in backend.reader.calls)
    assert all(call["thread"] != threading.get_ident() for call in backend.reader.calls)
    assert len(backend.loads) == 1
    assert backend.loads[0][1] != threading.get_ident()


@pytest.mark.asyncio
async def test_reader_stays_cached_across_batches(backend):
    payload = encode_batch([PageImage(7, _image_bytes())], SETTINGS)
    async with _client(ocr_server.create_app(SETTINGS)) as client:
        for _ in range(2):
            assert (await _post(client, payload)).status_code == 200
        assert (await client.get("/health")).json()["device"] == "cuda:0"
    assert len(backend.loads) == 1
    assert len(backend.reader.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"model": "latin_g2"},
    {"model_version": "easyocr-1.7.1:craft:english_g2"},
    {"protocol": 2},
])
async def test_incompatible_model_or_protocol_is_rejected(backend, overrides):
    payload = _archive([PageImage(7, _image_bytes())], overrides=overrides)
    async with _client(ocr_server.create_app(SETTINGS)) as client:
        response = await _post(client, payload)
    assert response.status_code == 409
    assert response.json() == {"detail": "OCR model or protocol version mismatch"}
    assert backend.reader.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [
    "not-a-zip", "extra-member", "compressed", "duplicate-id", "missing-id",
    "bad-manifest", "deep-manifest",
])
async def test_malformed_archives_are_safe_errors(backend, malformed):
    pages = [PageImage(7, _image_bytes())]
    if malformed == "not-a-zip":
        payload = b"INVENTED_PRIVATE_ARCHIVE_DETAIL"
    elif malformed == "extra-member":
        payload = _archive(pages, extra_member="../INVENTED_PRIVATE_ARCHIVE_DETAIL")
    elif malformed == "compressed":
        payload = _archive(pages, compression=zipfile.ZIP_DEFLATED)
    elif malformed == "duplicate-id":
        payload = _archive(pages, overrides={"pages": [7, 7]})
    elif malformed == "missing-id":
        payload = _archive(pages, overrides={"pages": [9]})
    else:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            manifest = (
                b"[" * 2000 + b"0" + b"]" * 2000
                if malformed == "deep-manifest"
                else b"INVENTED_PRIVATE_ARCHIVE_DETAIL"
            )
            archive.writestr("manifest.json", manifest)
        payload = buf.getvalue()
    async with _client(ocr_server.create_app(SETTINGS)) as client:
        response = await _post(client, payload)
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid OCR batch"}
    assert backend.reader.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_png", [
    b"INVENTED_PRIVATE_IMAGE_DETAIL",
    _image_bytes(image_format="JPEG"),
    _image_bytes()[:-8],
    _invalid_zlib_png(),
    _oversized_png(),
    _animated_png(),
])
async def test_malformed_or_oversized_images_are_rejected(backend, invalid_png):
    payload = encode_batch([PageImage(7, invalid_png)], SETTINGS)
    async with _client(ocr_server.create_app(SETTINGS)) as client:
        response = await _post(client, payload)
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid PNG page image"}
    assert backend.reader.calls == []


@pytest.mark.asyncio
async def test_invalid_later_page_prevents_any_inference(backend):
    payload = encode_batch([
        PageImage(7, _image_bytes()),
        PageImage(9, b"INVENTED_PRIVATE_IMAGE_DETAIL"),
    ], SETTINGS)
    async with _client(ocr_server.create_app(SETTINGS)) as client:
        assert (await _post(client, payload)).status_code == 400
    assert backend.reader.calls == []


@pytest.mark.asyncio
async def test_wrong_content_type_is_rejected(backend):
    async with _client(ocr_server.create_app(SETTINGS)) as client:
        response = await client.post("/v1/ocr", json={"invented": "not an OCR request"})
    assert response.status_code == 415
    assert backend.reader.calls == []


@pytest.mark.asyncio
async def test_streaming_size_cap_stops_reading_oversized_request(backend):
    chunks_sent = 0
    chunk = b"x" * (1024 * 1024)

    async def oversized_body():
        nonlocal chunks_sent
        for _ in range(MAX_BYTES // len(chunk) + 2):
            chunks_sent += 1
            yield chunk

    async with _client(ocr_server.create_app(SETTINGS)) as client:
        response = await _post(client, oversized_body())
    assert response.status_code == 413
    assert chunks_sent == MAX_BYTES // len(chunk) + 1
    assert backend.reader.calls == []


@pytest.mark.asyncio
async def test_backend_failure_is_generic_and_service_remains_healthy(backend, caplog):
    backend.reader.fail = True
    payload = encode_batch([PageImage(7, _image_bytes())], SETTINGS)
    async with _client(ocr_server.create_app(SETTINGS)) as client:
        response = await _post(client, payload)
        assert response.status_code == 503
        assert response.json() == {"detail": "OCR inference unavailable"}
        assert health_ready((await client.get("/health")).json(), SETTINGS)
        backend.reader.fail = False
        assert (await _post(client, payload)).status_code == 200
    assert "INVENTED_PRIVATE_BACKEND_DETAIL" not in caplog.text


@pytest.mark.asyncio
async def test_health_is_responsive_and_batches_are_exclusive(backend):
    reader = backend.reader
    reader.release.clear()
    payload = encode_batch([PageImage(7, _image_bytes())], SETTINGS)
    async with _client(ocr_server.create_app(SETTINGS)) as client:
        requests = [asyncio.create_task(_post(client, payload))]
        try:
            assert await asyncio.to_thread(reader.entered.wait, 2)
            requests.append(asyncio.create_task(_post(client, payload)))
            health = await asyncio.wait_for(client.get("/health"), timeout=1)
            assert health.status_code == 200
            assert health_ready(health.json(), SETTINGS)
            assert not await asyncio.to_thread(reader.second_entered.wait, 0.1)
            assert not requests[1].done()
            assert reader.max_active == 1
        finally:
            reader.release.set()
            responses = await asyncio.gather(*requests, return_exceptions=True)
    assert len(responses) == 2
    assert all(response.status_code == 200 for response in responses)
    assert reader.max_active == 1


@pytest.mark.asyncio
async def test_cancelled_request_keeps_gpu_slot_until_thread_finishes(backend):
    reader = backend.reader
    reader.release.clear()
    payload = encode_batch([PageImage(7, _image_bytes())], SETTINGS)
    async with _client(ocr_server.create_app(SETTINGS)) as client:
        first = asyncio.create_task(_post(client, payload))
        second = None
        try:
            assert await asyncio.to_thread(reader.entered.wait, 2)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(_post(client, payload))
            health = await asyncio.wait_for(client.get("/health"), timeout=1)
            assert health_ready(health.json(), SETTINGS)
            assert not await asyncio.to_thread(reader.second_entered.wait, 0.1)
            assert not second.done()
            assert reader.max_active == 1
        finally:
            reader.release.set()
            if second is not None:
                response = await second
            await asyncio.gather(first, return_exceptions=True)
    assert response.status_code == 200
    assert len(reader.calls) == 2
    assert reader.max_active == 1


@pytest.mark.asyncio
async def test_waiting_batch_does_not_buffer_body_while_gpu_is_busy(backend):
    reader = backend.reader
    reader.release.clear()
    payload = encode_batch([PageImage(7, _image_bytes())], SETTINGS)
    body_read = asyncio.Event()
    waiting_started = asyncio.Event()

    async def chunks():
        body_read.set()
        yield payload

    async with _client(ocr_server.create_app(SETTINGS)) as client:
        first = asyncio.create_task(_post(client, payload))

        async def upload():
            waiting_started.set()
            return await client.post("/v1/ocr", content=chunks(),
                                     headers={"content-type": "application/zip"})

        second = None
        try:
            assert await asyncio.to_thread(reader.entered.wait, 2)
            second = asyncio.create_task(upload())
            await waiting_started.wait()
            health = await client.get("/health")
            assert health_ready(health.json(), SETTINGS)
            assert not body_read.is_set()
        finally:
            reader.release.set()
            responses = await asyncio.gather(first, *([second] if second else []))
    assert all(response.status_code == 200 for response in responses)
    assert body_read.is_set()


@pytest.mark.asyncio
async def test_startup_requires_pinned_engine_version(monkeypatch):
    monkeypatch.setattr(ocr_server.metadata, "version", lambda _: "1.7.1")
    app = ocr_server.create_app(SETTINGS)
    with pytest.raises(RuntimeError, match="requires EasyOCR 1.7.2"):
        async with app.router.lifespan_context(app):
            pytest.fail("An incompatible engine must not start")


@pytest.mark.asyncio
async def test_startup_requires_cuda(monkeypatch):
    monkeypatch.setattr(ocr_server.metadata, "version", lambda _: "1.7.2")
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
    ))
    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(Reader=lambda *args, **kw: None))
    app = ocr_server.create_app(SETTINGS)
    with pytest.raises(RuntimeError, match="requires CUDA"):
        async with app.router.lifespan_context(app):
            pytest.fail("A CPU-only engine must not start")


@pytest.mark.asyncio
async def test_reader_cpu_fallback_is_refused(monkeypatch):
    monkeypatch.setattr(ocr_server.metadata, "version", lambda _: "1.7.2")
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True),
    ))
    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(
        Reader=lambda *args, **kw: SimpleNamespace(device="cpu"),
    ))
    app = ocr_server.create_app(SETTINGS)
    with pytest.raises(RuntimeError, match="must use CUDA device 0"):
        async with app.router.lifespan_context(app):
            pytest.fail("An EasyOCR CPU fallback must not start")


