"""Scheduling-policy tests (authored at the golden-reference flip of
nbs/core/scheduling.ipynb — the notebook carried NO executable tests; these
pin current behavior). NOTE: the quantity checks read manifest fields
(min_gpu_vram_mb / min_system_ram_mb) that V12 declares dropped at the CR-7
reactive-resource reframe — the tests pin the vestigial-but-live semantics;
see the on-graph FINDING from the module-16/26 triage."""

import asyncio

from cjm_substrate.core.metadata import CapabilityMeta
from cjm_substrate.core.scheduling import (PermissiveScheduler,
                                           QueueScheduler, SafetyScheduler)


def _meta(name="cap", resources=None):
    meta = CapabilityMeta(name=name, version="0.0.1", description="test cap",
                          config_schema={})
    if resources is not None:
        meta.manifest = {"resources": resources}
    return meta


def test_permissive_scheduler_allows_everything():
    sched = PermissiveScheduler()
    assert sched.allocate(_meta(), lambda: {}) is True
    # Lifecycle notifications are no-ops
    sched.on_execution_start("cap")
    sched.on_execution_finish("cap")


def test_allocate_async_default_delegates_to_sync():
    sched = PermissiveScheduler()

    calls = []

    async def stats_provider():
        calls.append(1)
        return {}

    assert asyncio.run(sched.allocate_async(_meta(), stats_provider)) is True
    assert calls == [1], "default async path fetches stats exactly once"


def test_safety_scheduler_allows_without_manifest_or_requirements():
    sched = SafetyScheduler()
    assert sched.allocate(_meta(), lambda: {}) is True  # no .manifest at all
    assert sched.allocate(_meta(resources={}), lambda: {}) is True


def test_safety_scheduler_gpu_capability_without_gpu_stats_allows():
    # No GPU telemetry -> warn to stderr and allow (fail-open posture)
    sched = SafetyScheduler()
    meta = _meta(resources={"requires_gpu": True, "min_gpu_vram_mb": 4096})
    assert sched.allocate(meta, lambda: {}) is True


def test_safety_scheduler_blocks_on_insufficient_vram():
    sched = SafetyScheduler()
    meta = _meta(resources={"requires_gpu": True, "min_gpu_vram_mb": 4096})
    assert sched.allocate(meta, lambda: {"gpu_free_memory_mb": 1024}) is False
    assert sched.allocate(meta, lambda: {"gpu_free_memory_mb": 8192}) is True


def test_safety_scheduler_blocks_on_insufficient_ram():
    sched = SafetyScheduler()
    meta = _meta(resources={"min_system_ram_mb": 8192})
    assert sched.allocate(meta, lambda: {"memory_available_mb": 2048}) is False
    assert sched.allocate(meta, lambda: {"memory_available_mb": 16384}) is True


def test_queue_scheduler_returns_when_resources_free_up():
    sched = QueueScheduler(timeout=5.0, poll_interval=0.01)
    meta = _meta(resources={"min_system_ram_mb": 8192})
    readings = iter([{"memory_available_mb": 1024},
                     {"memory_available_mb": 1024},
                     {"memory_available_mb": 16384}])
    assert sched.allocate(meta, lambda: next(readings)) is True


def test_queue_scheduler_times_out():
    sched = QueueScheduler(timeout=0.03, poll_interval=0.01)
    meta = _meta(resources={"min_system_ram_mb": 8192})
    assert sched.allocate(meta, lambda: {"memory_available_mb": 1024}) is False


def test_queue_scheduler_async_waits_and_times_out():
    sched = QueueScheduler(timeout=5.0, poll_interval=0.01)
    meta = _meta(resources={"min_system_ram_mb": 8192})

    readings = iter([{"memory_available_mb": 1024},
                     {"memory_available_mb": 16384}])

    async def stats_provider():
        return next(readings)

    assert asyncio.run(sched.allocate_async(meta, stats_provider)) is True

    impatient = QueueScheduler(timeout=0.03, poll_interval=0.01)

    async def always_busy():
        return {"memory_available_mb": 1024}

    assert asyncio.run(impatient.allocate_async(meta, always_busy)) is False


def test_queue_scheduler_active_capability_tracking():
    sched = QueueScheduler()
    assert sched.get_active_capabilities() == set()
    sched.on_execution_start("whisper")
    sched.on_execution_start("demucs")
    assert sched.get_active_capabilities() == {"whisper", "demucs"}
    sched.on_execution_finish("whisper")
    sched.on_execution_finish("never-started")  # discard, not remove
    assert sched.get_active_capabilities() == {"demucs"}
    # Copy semantics: mutating the returned set doesn't touch the tracker
    sched.get_active_capabilities().clear()
    assert sched.get_active_capabilities() == {"demucs"}
