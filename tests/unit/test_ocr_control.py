"""Dedicated user-unit state transitions; no live SSH or Amazon credentials."""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from kindle.ocr_config import UNIT, OcrSettings
from kindle.ocr_control import OcrHostError, ensure_ocr_host_ready, status_ocr_host, stop_ocr_host


def settings(**kwargs):
    return OcrSettings(base_url="http://ocr.test:9883", ssh_target="operator@ocr.test", **kwargs)


async def unavailable(url):
    return None


async def ready(url):
    config = settings()
    return {
        "protocol": 1, "model": config.model, "model_version": config.version,
        "warm": True, "device": "cuda:0",
    }


class Unit:
    """Stateful dedicated-unit fake; rejects any unrelated unit or operation."""

    def __init__(self, active=False, status_code=None, start_code=0):
        self.active = active
        self.status_code = status_code
        self.start_code = start_code
        self.actions = []

    def __call__(self, argv):
        assert argv[-1] == UNIT
        action = argv[-2]
        self.actions.append(action)
        if action == "is-active":
            code = self.status_code if self.status_code is not None else (0 if self.active else 3)
        elif action == "start":
            code = self.start_code
            self.active = code == 0
        elif action == "stop":
            code = 0
            self.active = False
        else:
            raise AssertionError(f"Unexpected mutation: {action}")
        return subprocess.CompletedProcess(argv, code)


async def test_start_then_explicit_stop_transitions_only_dedicated_unit():
    unit = Unit()
    waited = []

    async def warm(url, timeout):
        assert unit.active
        waited.append(url)

    assert await ensure_ocr_host_ready(settings(), run=unit, probe=unavailable, wait_warm=warm) == "started"
    assert unit.actions == ["is-active", "start"]
    assert waited == ["http://ocr.test:9883"]
    assert await stop_ocr_host(settings(manage_gpu="never"), run=unit) == "stopped"
    assert unit.active is False
    assert unit.actions == ["is-active", "start", "stop"]


async def test_healthy_endpoint_touches_no_unit_and_does_not_wait():
    def forbidden(*args):
        raise AssertionError("Healthy endpoint must not run SSH or systemctl")

    async def forbidden_wait(*args):
        raise AssertionError("Healthy endpoint must not wait")

    assert await ensure_ocr_host_ready(settings(), run=forbidden, probe=ready, wait_warm=forbidden_wait) == "already-running"
    assert await status_ocr_host(settings(), run=forbidden, probe=ready) == "already-running"


@pytest.mark.parametrize("config", [OcrSettings(), settings(manage_gpu="never")])
async def test_skipped_does_not_probe_control_or_wait(config):
    def forbidden(*args):
        raise AssertionError("Skipped must have no effects")

    assert await ensure_ocr_host_ready(config, run=forbidden, probe=forbidden, wait_warm=forbidden) == "skipped"


async def test_active_unit_waits_for_readiness_without_restart():
    unit = Unit(active=True)
    waited = []

    async def warm(url, timeout):
        waited.append(url)

    assert await ensure_ocr_host_ready(settings(), run=unit, probe=unavailable, wait_warm=warm) == "already-running"
    assert unit.actions == ["is-active"]
    assert waited == ["http://ocr.test:9883"]


@pytest.mark.parametrize("code", [4, 1, 255])
async def test_failed_status_never_attempts_start(code):
    unit = Unit(status_code=code)
    with pytest.raises(OcrHostError):
        await ensure_ocr_host_ready(settings(), run=unit, probe=unavailable)
    assert unit.actions == ["is-active"]


async def test_start_failure_does_not_wait_or_stop():
    unit = Unit(start_code=1)
    with pytest.raises(OcrHostError):
        await ensure_ocr_host_ready(settings(), run=unit, probe=unavailable)
    assert unit.actions == ["is-active", "start"]
    assert not unit.active


async def test_warm_timeout_leaves_service_running():
    unit = Unit()

    async def never_warm(url, timeout):
        await asyncio.Event().wait()

    with pytest.raises(OcrHostError):
        await ensure_ocr_host_ready(settings(start_timeout=0.02), run=unit, probe=unavailable, wait_warm=never_warm)
    assert unit.active
    assert unit.actions == ["is-active", "start"]


async def test_version_mismatch_fails_before_control():
    unit = Unit()

    async def wrong_version(url):
        health = await ready(url)
        return {**health, "model_version": "incompatible"}

    with pytest.raises(OcrHostError):
        await ensure_ocr_host_ready(settings(), run=unit, probe=wrong_version)
    assert unit.actions == []


async def test_status_observes_inactive_without_starting():
    unit = Unit()
    assert await status_ocr_host(settings(), run=unit, probe=unavailable) == "inactive"
    assert unit.actions == ["is-active"]
