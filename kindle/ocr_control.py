"""Control only the Kindle OCR user unit; never stop it implicitly.

Explicit ``stop`` ignores manage_gpu=never: that setting disables automatic
startup, not an operator's deliberate shutdown of the configured OCR service.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import socket
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from .ocr_config import UNIT, OcrSettings
from .ocr_wire import OcrProtocolError, health_ready

POLL_INTERVAL = 2.0
Probe = Callable[[str], Awaitable[Any]]
Runner = Callable[[list[str]], Any]


class OcrHostError(RuntimeError):
    """The dedicated OCR unit could not be controlled or become ready."""


@dataclass(frozen=True)
class HostControl:
    kind: Literal["local", "ssh"]
    target: str | None = None


def resolve_control(base_url: str, ssh_target: str | None = None) -> HostControl:
    if ssh_target:
        return HostControl("ssh", ssh_target)
    host = (urlsplit(base_url).hostname or "").lower()
    try:
        names = {socket.getfqdn().lower(), socket.gethostname().lower()}
        names |= {name.split(".")[0] for name in names}
    except OSError:
        names = set()
    if host in {"localhost", "127.0.0.1", "::1"} | names:
        return HostControl("local")
    return HostControl("ssh", host)


def control_command(control: HostControl, action: str) -> list[str]:
    if action not in {"is-active", "start", "stop"}:
        raise OcrHostError("Unsupported OCR unit action")
    local = ["systemctl", "--user", action, UNIT]
    if control.kind == "local":
        return local
    target = control.target
    if not target or target.startswith("-") or any(
        not (c.isalnum() or c in "@._-:[]") for c in target
    ):
        raise OcrHostError("Invalid OCR SSH target")
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", target, *local]


def _default_run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)


async def _run(control: HostControl, action: str, runner: Runner | None) -> int:
    try:
        result = await asyncio.to_thread(runner or _default_run, control_command(control, action))
        if inspect.isawaitable(result):
            result = await result
        return result.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        # Subprocess output and exception strings may contain credentials.
        raise OcrHostError(f"OCR unit {action} failed ({type(exc).__name__})") from None


async def _default_probe(base_url: str) -> Any:
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
        try:
            response = await client.get(base_url.rstrip("/") + "/health")
            if response.status_code != 200:
                return None
            return response.json()
        except httpx.HTTPError:
            return None
        except ValueError:
            raise OcrHostError("Invalid OCR health response") from None


async def _ready(settings: OcrSettings, probe: Probe | None) -> bool | None:
    try:
        value = await (probe or _default_probe)(settings.base_url)
    except (httpx.HTTPError, OSError):
        return None
    if value is None:
        return None
    try:
        return health_ready(value, settings)
    except OcrProtocolError:
        raise OcrHostError("OCR health protocol/model version mismatch") from None


async def _default_wait_warm(settings: OcrSettings, probe: Probe | None = None) -> None:
    async def poll() -> None:
        while True:
            if await _ready(settings, probe):
                return
            await asyncio.sleep(POLL_INTERVAL)

    try:
        await asyncio.wait_for(poll(), timeout=settings.start_timeout)
    except TimeoutError:
        raise OcrHostError("OCR service did not become warm before startup deadline") from None


async def _wait(settings: OcrSettings, waiter: Callable | None, probe: Probe | None) -> None:
    try:
        if waiter is None:
            await _default_wait_warm(settings, probe)
        else:
            await asyncio.wait_for(
                waiter(settings.base_url, settings.start_timeout), settings.start_timeout
            )
    except TimeoutError:
        raise OcrHostError("OCR service did not become warm before startup deadline") from None


def _active_error(code: int) -> OcrHostError:
    if code == 4:
        return OcrHostError(f"Dedicated OCR user unit {UNIT} is missing")
    return OcrHostError(f"OCR unit status failed (exit {code}); no start attempted")


async def ensure_ocr_host_ready(
    settings: OcrSettings, *, run: Runner | None = None,
    wait_warm: Callable | None = None, probe: Probe | None = None,
) -> str:
    """Return started/already-running/skipped; HTTP readiness precedes control.

    ``run(argv)`` may be synchronous or asynchronous. ``probe(base_url)`` is
    asynchronous and returns a health object, or None for unavailable.
    ``wait_warm(base_url, timeout)`` is asynchronous and must validate readiness.
    """
    if not settings.base_url or settings.manage_gpu == "never":
        return "skipped"
    ready = await _ready(settings, probe)
    if ready is True:
        return "already-running"
    if ready is False:
        await _wait(settings, wait_warm, probe)
        return "already-running"
    control = resolve_control(settings.base_url, settings.ssh_target)
    code = await _run(control, "is-active", run)
    if code not in {0, 3}:
        raise _active_error(code)
    if code == 0:
        await _wait(settings, wait_warm, probe)
        return "already-running"
    code = await _run(control, "start", run)
    if code != 0:
        raise OcrHostError(f"OCR unit start failed (exit {code})")
    await _wait(settings, wait_warm, probe)
    return "started"


async def stop_ocr_host(settings: OcrSettings, *, run: Runner | None = None) -> str:
    """Explicitly stop the dedicated unit, even with manage_gpu=never."""
    if not settings.base_url:
        return "skipped"
    code = await _run(resolve_control(settings.base_url, settings.ssh_target), "stop", run)
    if code != 0:
        raise OcrHostError(f"OCR unit stop failed (exit {code})")
    return "stopped"


async def status_ocr_host(
    settings: OcrSettings, *, run: Runner | None = None, probe: Probe | None = None,
) -> str:
    """Inspect readiness or unit activity without starting or stopping anything."""
    if not settings.base_url:
        return "skipped"
    ready = await _ready(settings, probe)
    if ready is not None:
        return "already-running" if ready else "warming"
    code = await _run(resolve_control(settings.base_url, settings.ssh_target), "is-active", run)
    if code == 0:
        return "warming"
    if code == 3:
        return "inactive"
    raise _active_error(code)


def main() -> int:
    parser = argparse.ArgumentParser(description="Control the dedicated Kindle OCR user service")
    parser.add_argument("action", choices=("start", "stop", "status"))
    args = parser.parse_args()
    try:
        settings = OcrSettings.from_env()
        operation = {"start": ensure_ocr_host_ready, "stop": stop_ocr_host, "status": status_ocr_host}[args.action]
        print(asyncio.run(operation(settings)))
        return 0
    except (OcrHostError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
