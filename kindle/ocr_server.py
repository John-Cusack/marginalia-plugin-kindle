"""CUDA-only Kindle OCR service: ``python -m kindle.ocr_server``.

Install the server extras and CUDA PyTorch on the GPU host. The reader stays
resident until the dedicated user service is explicitly stopped; there is no
idle exit. Request bodies and recognized text are never logged.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import struct
import warnings
from contextlib import asynccontextmanager
from importlib import metadata
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .ocr_config import EASYOCR_VERSION, MAX_BYTES, OcrSettings
from .ocr_wire import PROTOCOL, OcrProtocolError, OcrVersionError, PageImage, decode_batch

MAX_IMAGE_PIXELS = 4_000_000
RECOGNITION_BATCH_SIZE = 32
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class InvalidPageImage(ValueError):
    """An image cannot be safely decoded as a bounded, single-frame PNG."""


def _load_reader(settings: OcrSettings) -> Any:
    """Load exactly the requested CUDA engine, never EasyOCR's CPU fallback."""
    try:
        installed_version = metadata.version("easyocr")
    except metadata.PackageNotFoundError:
        raise RuntimeError("Install the OCR server extras and CUDA PyTorch") from None
    if installed_version != EASYOCR_VERSION:
        raise RuntimeError("The OCR server requires EasyOCR 1.7.2")
    try:
        import easyocr
        import torch
    except ImportError:
        raise RuntimeError("Install the OCR server extras and CUDA PyTorch") from None
    if not torch.cuda.is_available():
        raise RuntimeError("The OCR server requires CUDA; CPU fallback is disabled")
    try:
        reader = easyocr.Reader(
            ["en"],
            gpu=True,
            detect_network="craft",
            recog_network=settings.model,
            verbose=False,
        )
    except Exception:
        raise RuntimeError("Failed to initialize the CUDA OCR reader") from None
    if str(reader.device) not in {"cuda", "cuda:0"}:
        raise RuntimeError("The OCR reader must use CUDA device 0")
    return reader


def _validate_png(png: bytes) -> None:
    """Bound dimensions from the PNG header before any decoded-pixel allocation."""
    if (
        len(png) < 33
        or png[:8] != PNG_SIGNATURE
        or png[8:16] != b"\x00\x00\x00\rIHDR"
    ):
        raise InvalidPageImage("Invalid PNG")
    width, height = struct.unpack_from(">II", png, 16)
    if not width or not height or width * height > MAX_IMAGE_PIXELS:
        raise InvalidPageImage("Invalid PNG dimensions")

    from PIL import Image

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(png)) as image:
                if image.format != "PNG" or image.size != (width, height):
                    raise InvalidPageImage("Invalid PNG")
                if getattr(image, "n_frames", 1) != 1:
                    raise InvalidPageImage("Animated PNG is not supported")
                image.verify()
    except InvalidPageImage:
        raise
    except Exception:
        raise InvalidPageImage("Invalid PNG") from None


def _recognize_pages(reader: Any, pages: list[PageImage]) -> list[dict[str, Any]]:
    """Only one page's decoded pixels occupy host/GPU memory at a time."""
    import numpy as np
    from PIL import Image

    for page in pages:
        _validate_png(page.png)
    results = []
    for page in pages:
        try:
            with Image.open(io.BytesIO(page.png)) as image:
                pixels = np.asarray(image.convert("RGB"))
        except Exception:
            raise InvalidPageImage("Invalid PNG") from None
        text = "\n".join(
            reader.readtext(
                pixels,
                detail=0,
                paragraph=True,
                batch_size=RECOGNITION_BATCH_SIZE,
            )
        )
        results.append({"page_id": page.page_id, "text": text})
        del pixels
    return results


def create_app(settings: OcrSettings | None = None) -> FastAPI:
    """Build a separately-owned, warm GPU reader and versioned ZIP endpoint."""
    settings = settings or OcrSettings.from_env()
    reader: Any = None
    gpu = asyncio.Semaphore(1)
    running: set[asyncio.Task[list[dict[str, Any]]]] = set()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal reader
        reader = await asyncio.to_thread(_load_reader, settings)
        try:
            yield
        finally:
            if running:
                await asyncio.shield(asyncio.gather(*running, return_exceptions=True))
            reader = None

    app = FastAPI(title="Marginalia Kindle OCR", lifespan=lifespan)

    def version_fields() -> dict[str, Any]:
        return {
            "protocol": PROTOCOL,
            "model": settings.model,
            "model_version": settings.version,
        }

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {**version_fields(), "warm": reader is not None, "device": "cuda:0"}

    def recognize(payload: bytes) -> list[dict[str, Any]]:
        pages = decode_batch(payload, settings)
        return _recognize_pages(reader, pages)

    def finished(task: asyncio.Task[list[dict[str, Any]]]) -> None:
        running.discard(task)
        # A disconnected requester may never retrieve the inference exception.
        # Consume it without logging anything supplied by an image or backend.
        if not task.cancelled():
            task.exception()
        gpu.release()

    @app.post("/v1/ocr")
    async def ocr(request: Request) -> dict[str, Any]:
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/zip":
            raise HTTPException(status_code=415, detail="Expected application/zip")
        if reader is None:
            raise HTTPException(status_code=503, detail="OCR service is not ready")
        # Admit before buffering so queued uploads cannot each retain 32 MiB.
        # /health never takes this semaphore.
        await gpu.acquire()
        try:
            payload = bytearray()
            async for chunk in request.stream():
                if len(payload) + len(chunk) > MAX_BYTES:
                    raise HTTPException(status_code=413, detail="OCR batch exceeds 32 MiB")
                payload.extend(chunk)
            task = asyncio.create_task(asyncio.to_thread(recognize, bytes(payload)))
            del payload
        except BaseException:
            gpu.release()
            raise
        running.add(task)
        # The worker owns the slot even if its requester disconnects.
        task.add_done_callback(finished)
        try:
            results = await asyncio.shield(task)
        except OcrVersionError:
            raise HTTPException(status_code=409, detail="OCR model or protocol version mismatch") from None
        except OcrProtocolError:
            raise HTTPException(status_code=400, detail="Invalid OCR batch") from None
        except InvalidPageImage:
            raise HTTPException(status_code=400, detail="Invalid PNG page image") from None
        except Exception:
            raise HTTPException(status_code=503, detail="OCR inference unavailable") from None
        return {**version_fields(), "results": results}

    return app


def main(argv: list[str] | None = None) -> None:
    """Run until stopped; bind only to loopback unless explicitly configured."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=os.getenv("KINDLE_OCR_BIND_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("KINDLE_OCR_PORT", "9883")))
    args = parser.parse_args(argv)

    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
