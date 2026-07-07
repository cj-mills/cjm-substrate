"""Scheduling-policy tests (authored at the golden-reference flip of
nbs/core/scheduling.ipynb). The quantity-based SafetyScheduler / QueueScheduler
(and their pinning tests) were deleted when the 76441c91 seam-fate was ratified:
they read manifest fields V12 dropped at the CR-7 reactive-resource reframe and
had degenerated to always-allow — resource safety lives in the empirical
admission layer now. What remains is the seam: the ResourceScheduler protocol +
the PermissiveScheduler default."""

import asyncio

from cjm_substrate.core.metadata import CapabilityMeta
from cjm_substrate.core.scheduling import PermissiveScheduler


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
