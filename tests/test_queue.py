"""JobQueue tests (projected from nbs/core/queue.ipynb #|hide cells at the
golden-reference flip): SG-13 eviction regression, CR-6 stage-1 event bus,
stage-3 composition + multi-lane admission ladder, stage-3 resource snapshots,
CR-14 journal-primary emission + wedge gate, CR-14 follow-up run_id/actor
correlation + ADMISSION_DECIDED, CR-6 stage-4 cancel-phase/retry/block-reason,
and the stage-4 task channel.

The notebook's structural-validation check caught a generic Exception and
substring-matched the message — upgraded to typed pytest.raises against
CompositionValidationError."""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from cjm_substrate.core.diagnostics_store import (
    DiagnosticRecord, LocalDiagnosticsStore,
)
from cjm_substrate.core.errors import (
    CapabilityDisabledError, CapabilityInputError, CapabilityResourceError,
)
from cjm_substrate.core.journal_store import (
    LocalJournalStore, SubstrateEventType,
)
from cjm_substrate.core.ports import (
    Composition, CompositionNode, CompositionValidationError, NodeState,
    OutputRef,
)
from cjm_substrate.core.queue import (
    CancelPhase, Job, JobEvent, JobEventType, JobQueue, JobQueueDependencies,
    JobStatus, _subscriber_keys_for,
)
from cjm_substrate.core.wire import get_call_envelope


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

class FakeDeps:
    """Minimal concrete fake satisfying JobQueueDependencies.

    Used for the isinstance-against-Protocol assertion because Python 3.12+
    runtime_checkable Protocol checks use `inspect.getattr_static()` which
    bypasses MagicMock's __getattr__-driven attribute auto-creation. A real
    class with declared methods satisfies the static-attribute lookup.
    """
    def get_capability_meta(self, name_or_id): return None
    def get_capability(self, name_or_id): return None
    async def execute_capability_async(self, name_or_id, *args, **kwargs): return None
    def reload_capability(self, name_or_id): return None
    def get_admission_profile(self, name_or_id): return None
    def get_instance_concurrency_cap(self, name_or_id): return None
    async def get_global_stats(self): return {}
    async def execute_capability_task_async(self, name_or_id, task_name, method, **kwargs): return None


@dataclass
class FakeMeta:
    enabled: bool = True


class AdmissionDeps:
    """Driver fake with the stage-3 admission surface.

    Reactors map capability_instance_id -> callable(*args, **kwargs) -> result
    (raise to fail the job). `profiles` / `caps` / `stats` drive admission;
    concurrency high-water marks are tracked globally and per instance so
    tests can assert what actually co-ran.
    """
    def __init__(self, profiles=None, caps=None, stats=None, disabled=()):
        self._reactors = {}
        self.profiles = profiles or {}
        self.caps = caps or {}
        self.stats = stats if stats is not None else {}
        self.disabled = set(disabled)
        self.call_log = []
        self.active = 0
        self.max_active = 0
        self.active_by_instance = {}
        self.max_active_by_instance = {}

    def register(self, name, reactor):
        self._reactors[name] = reactor

    def get_capability_meta(self, name_or_id):
        return FakeMeta(enabled=name_or_id not in self.disabled)

    def get_capability(self, name_or_id):
        return object()  # no get_progress / get_stats -> polling no-ops

    async def execute_capability_async(self, name_or_id, *args, **kwargs):
        self.call_log.append((name_or_id, args, kwargs))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        cur = self.active_by_instance.get(name_or_id, 0) + 1
        self.active_by_instance[name_or_id] = cur
        self.max_active_by_instance[name_or_id] = max(
            self.max_active_by_instance.get(name_or_id, 0), cur)
        try:
            reactor = self._reactors.get(name_or_id)
            res = reactor(*args, **kwargs) if callable(reactor) else reactor
            if asyncio.iscoroutine(res):
                res = await res
            return res
        finally:
            self.active -= 1
            self.active_by_instance[name_or_id] -= 1

    def reload_capability(self, name_or_id):
        return None

    def get_admission_profile(self, name_or_id):
        return self.profiles.get(name_or_id)

    def get_instance_concurrency_cap(self, name_or_id):
        return self.caps.get(name_or_id)

    async def get_global_stats(self):
        return self.stats


CPU_PROFILE = {"gpu_memory_mb_peak_max": 0.0, "memory_mb_peak_max": 50.0, "sample_count": 5}
SYS_STATS = {"gpu_free_memory_mb": 20000.0, "gpu_total_memory_mb": 24000.0,
             "memory_available_mb": 64000.0}


async def _slow(**kwargs):
    await asyncio.sleep(0.15)
    return {"ok": True}


class FakeWorkerProxy:
    """Worker proxy fake: returns deterministic get_stats payload."""
    def __init__(self, pid=12345, cpu=42.0, rss=512.0):
        self._stats = {'pid': pid, 'cpu_percent': cpu, 'memory_rss_mb': rss}

    def get_stats(self):
        return self._stats


class FakeSysmon:
    """CR-3 typed MonitorToolProtocol fake: get_system_status + list_processes."""
    def __init__(self, worker_pid=12345):
        self._worker_pid = worker_pid

    def get_system_status(self):
        return {
            'gpu_type': 'NVIDIA',
            'gpu_total_memory_mb': 24000.0,
            'gpu_load_percent': 78.0,
        }

    def list_processes(self):
        return [
            {'pid': 99999, 'gpu_index': 0, 'gpu_memory_mb': 100.0},
            {'pid': self._worker_pid, 'gpu_index': 0, 'gpu_memory_mb': 8500.0},
        ]


class ProxySysmonDeps:
    """Driver fake supporting both worker-proxy + sysmon-capability lookups."""
    def __init__(self, worker_proxy=None, sysmon=None):
        self._worker = worker_proxy
        self._sysmon = sysmon

    def get_capability_meta(self, name_or_id):
        return FakeMeta()

    def get_capability(self, name_or_id):
        if name_or_id == 'sysmon':
            return self._sysmon
        return self._worker

    async def execute_capability_async(self, name_or_id, *args, **kwargs):
        return f"result-{args[0] if args else ''}"

    def reload_capability(self, name_or_id):
        return None


async def _collect(gen, sink, limit):
    async for evt in gen:
        sink.append(evt)
        if len(sink) >= limit:
            return


# ---------------------------------------------------------------------------
# SG-13 regression: eviction from _job_completed_events must signal waiters
# ---------------------------------------------------------------------------

def test_sg13_eviction_signals_waiters():
    async def scenario():
        queue = JobQueue(deps=MagicMock(), max_history=2)

        # Submit-style setup: 3 jobs + their events without actually running.
        jobs = []
        for i in range(3):
            j = Job(id=f"job-{i}", capability_instance_id="p", args=(), kwargs={})
            j.status = JobStatus.completed
            jobs.append(j)
            queue._jobs[j.id] = j
            queue._job_completed_events[j.id] = asyncio.Event()

        # A waiter grabs a reference to job-0's event BEFORE eviction.
        held_ref = queue._job_completed_events["job-0"]
        assert not held_ref.is_set(), "event should start unset"

        # Move jobs 0 and 1 into history (no eviction yet at max_history=2).
        queue._move_to_history(jobs[0])
        queue._move_to_history(jobs[1])
        assert "job-0" in queue._job_completed_events

        # Moving job 2 evicts job 0; the fix must set job-0's event first.
        queue._move_to_history(jobs[2])
        assert "job-0" not in queue._job_completed_events, "evicted entry should be gone"
        assert held_ref.is_set(), "SG-13: evicted event must be set so waiters resolve"

        # A waiter that awaits the held reference resolves immediately.
        await asyncio.wait_for(held_ref.wait(), timeout=0.1)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# CR-6 stage 1: event bus fan-out + Protocol satisfaction
# ---------------------------------------------------------------------------

def test_protocol_is_runtime_checkable():
    assert isinstance(FakeDeps(), JobQueueDependencies), \
        "FakeDeps with the declared methods should satisfy JobQueueDependencies"


def test_subscriber_keys_routing():
    plain = JobEvent(type=JobEventType.STATE_TRANSITION, job_id="X",
                     capability_instance_id="p")
    assert _subscriber_keys_for(plain) == ["all", "job:X"]
    tagged = JobEvent(
        type=JobEventType.STATE_TRANSITION, job_id="X", capability_instance_id="p",
        composition_id="C", node_id="convert",
    )
    assert _subscriber_keys_for(tagged) == ["all", "job:X", "comp:C"]


def test_event_bus_multi_subscriber_fanout():
    async def scenario():
        queue = JobQueue(deps=MagicMock(), max_history=10)
        received_a, received_b = [], []
        job_id = "job-A"
        t_a = asyncio.create_task(_collect(queue.events(job_id), received_a, 1))
        t_b = asyncio.create_task(_collect(queue.events(job_id), received_b, 1))
        await asyncio.sleep(0.05)  # let both subscriptions register

        queue._publish_event(JobEvent(
            type=JobEventType.STATE_TRANSITION, job_id=job_id,
            capability_instance_id="p",
            payload={"from": "pending", "to": "running"},
        ))
        await asyncio.wait_for(asyncio.gather(t_a, t_b), timeout=1.0)
        assert len(received_a) == 1 and len(received_b) == 1, \
            "Both subscribers should receive the event"

    asyncio.run(scenario())


def test_event_bus_composition_tag_routing():
    async def scenario():
        queue = JobQueue(deps=MagicMock(), max_history=10)
        received_all, received_comp, received_job = [], [], []
        t_all = asyncio.create_task(_collect(queue.all_events(), received_all, 1))
        t_comp = asyncio.create_task(
            _collect(queue.events_for_composition("comp-C"), received_comp, 1))
        t_job = asyncio.create_task(_collect(queue.events("job-B"), received_job, 1))
        await asyncio.sleep(0.05)

        queue._publish_event(JobEvent(
            type=JobEventType.COMPOSITION_ADVANCED, job_id="job-B",
            capability_instance_id="p", composition_id="comp-C", node_id="align",
        ))
        await asyncio.wait_for(
            asyncio.gather(t_all, t_comp, t_job), timeout=1.0)
        assert len(received_all) == 1, "all-events firehose should receive every event"
        assert len(received_comp) == 1, "composition subscriber should receive tagged event"
        assert len(received_job) == 1, "job subscriber should receive its event"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Stage 3: composition execution (pipe binding, failure semantics, gates)
# ---------------------------------------------------------------------------

def test_composition_pipe_binding():
    # Execution-time binding: convert -> align (the qwen3-e2e shape).
    async def scenario():
        deps = AdmissionDeps(profiles={"ffmpeg": CPU_PROFILE, "qwen3": CPU_PROFILE},
                             stats=SYS_STATS)
        deps.register("ffmpeg", lambda **k: {"output_path": "/tmp/conv.wav",
                                             "received": dict(k)})
        deps.register("qwen3", lambda **k: {"items": [k["audio"]]})
        queue = JobQueue(deps=deps, max_history=20, progress_poll_interval=0.01)
        await queue.start()
        try:
            comp_id = await queue.submit_composition(Composition(nodes=[
                CompositionNode("convert", "ffmpeg",
                                {"action": "convert", "input_path": "/tmp/a.mp3"}),
                CompositionNode("align", "qwen3",
                                {"audio": OutputRef("convert", "output_path"),
                                 "text": "hello"}),
            ]))
            run = await queue.wait_for_composition(comp_id, timeout=5.0)
            assert run.status == NodeState.completed, run.status
            results = run.results_by_node()
            # The align node's kwargs were materialized from convert's result.
            align_call = next(c for c in deps.call_log if c[0] == "qwen3")
            assert align_call[2]["audio"] == "/tmp/conv.wav"
            assert align_call[2]["text"] == "hello"
            assert results["align"]["items"] == ["/tmp/conv.wav"]
            # Lazy member creation: align's job did not exist until convert
            # completed (call order proves it).
            assert [c[0] for c in deps.call_log] == ["ffmpeg", "qwen3"]
        finally:
            await queue.stop()

    asyncio.run(scenario())


def test_parallel_nodes_co_run():
    # Parallel fan-in (VAD || FA shape) with CPU profiles: both co-run.
    async def scenario():
        deps = AdmissionDeps(profiles={"vad": CPU_PROFILE, "fa": CPU_PROFILE},
                             stats=SYS_STATS)
        deps.register("vad", _slow)
        deps.register("fa", _slow)
        queue = JobQueue(deps=deps, max_history=20, progress_poll_interval=0.01)
        await queue.start()
        try:
            comp_id = await queue.submit_composition(Composition(nodes=[
                CompositionNode("vad", "vad", {"media_path": "/seg.wav"}),
                CompositionNode("fa", "fa", {"audio": "/seg.wav", "text": "t"}),
            ]))
            run = await queue.wait_for_composition(comp_id, timeout=5.0)
            assert run.status == NodeState.completed
            assert deps.max_active == 2, \
                f"parallel nodes with CPU profiles should co-run; max_active={deps.max_active}"
        finally:
            await queue.stop()

    asyncio.run(scenario())


def test_fail_fast_skips_dependents():
    # Producer fails -> dependent skipped, never invoked; run lands failed.
    async def scenario():
        deps = AdmissionDeps(profiles={"ffmpeg": CPU_PROFILE, "qwen3": CPU_PROFILE},
                             stats=SYS_STATS)

        def _boom(**k):
            raise RuntimeError("conversion exploded")
        deps.register("ffmpeg", _boom)
        deps.register("qwen3", lambda **k: {"items": []})
        queue = JobQueue(deps=deps, max_history=20, progress_poll_interval=0.01)
        await queue.start()
        try:
            comp_id = await queue.submit_composition(Composition(nodes=[
                CompositionNode("convert", "ffmpeg", {"input_path": "/a.mp3"}),
                CompositionNode("align", "qwen3",
                                {"audio": OutputRef("convert", "output_path")}),
            ]))
            run = await queue.wait_for_composition(comp_id, timeout=5.0)
            assert run.status == NodeState.failed, run.status
            assert run.node_runs["convert"].state == NodeState.failed
            assert run.node_runs["convert"].error is not None
            assert run.node_runs["align"].state == NodeState.skipped
            assert not any(c[0] == "qwen3" for c in deps.call_log), \
                "skipped node must never be invoked"
        finally:
            await queue.stop()

    asyncio.run(scenario())


def test_cancel_composition_before_dispatch():
    # cancel_composition on an unstarted queue: pending member cancelled,
    # downstream cancelled, run lands cancelled (user intent).
    async def scenario():
        deps = AdmissionDeps(profiles={"ffmpeg": CPU_PROFILE, "qwen3": CPU_PROFILE},
                             stats=SYS_STATS)
        queue = JobQueue(deps=deps, max_history=20, progress_poll_interval=0.01)
        comp_id = await queue.submit_composition(Composition(nodes=[
            CompositionNode("convert", "ffmpeg", {"input_path": "/a.mp3"}),
            CompositionNode("align", "qwen3",
                            {"audio": OutputRef("convert", "output_path")}),
        ]))
        assert await queue.cancel_composition(comp_id) is True
        run = queue.get_composition(comp_id)
        assert run.status == NodeState.cancelled, run.status
        assert run.cancel_requested is True
        assert run.node_runs["convert"].state == NodeState.cancelled
        assert run.node_runs["align"].state == NodeState.cancelled
        assert await queue.cancel_composition(comp_id) is False, "already terminal"

    asyncio.run(scenario())


def test_empty_composition_completes_at_submit():
    async def scenario():
        queue = JobQueue(deps=AdmissionDeps(), max_history=20)
        comp_id = await queue.submit_composition(Composition(nodes=[]))
        run = await queue.wait_for_composition(comp_id, timeout=1.0)
        assert run.status == NodeState.completed

    asyncio.run(scenario())


def test_disabled_capability_gate_at_submit():
    async def scenario():
        deps = AdmissionDeps(disabled={"qwen3"})
        queue = JobQueue(deps=deps, max_history=20)
        with pytest.raises(CapabilityDisabledError):
            await queue.submit_composition(Composition(nodes=[
                CompositionNode("align", "qwen3", {"audio": "/x.wav"})]))

    asyncio.run(scenario())


def test_structural_validation_at_submit():
    async def scenario():
        queue = JobQueue(deps=AdmissionDeps(), max_history=20)
        with pytest.raises(CompositionValidationError, match="ghost"):
            await queue.submit_composition(Composition(nodes=[
                CompositionNode("a", "ffmpeg", {"x": OutputRef("ghost")})]))

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Stage 3: multi-lane admission ladder
# ---------------------------------------------------------------------------

def test_per_instance_cap_defaults_to_one():
    # Same instance never co-runs without an explicit cap.
    async def scenario():
        deps = AdmissionDeps(profiles={"p": CPU_PROFILE}, stats=SYS_STATS)
        deps.register("p", _slow)
        queue = JobQueue(deps=deps, max_history=20, progress_poll_interval=0.01)
        await queue.start()
        try:
            ids = [await queue.submit("p") for _ in range(3)]
            for jid in ids:
                await queue.wait_for_job(jid, timeout=5.0)
            assert deps.max_active_by_instance["p"] == 1, \
                f"default per-instance cap is 1; got {deps.max_active_by_instance}"
        finally:
            await queue.stop()

    asyncio.run(scenario())


def test_per_instance_cap_opts_up():
    # ... and opts UP via max_concurrent_requests (the ffmpeg case).
    async def scenario():
        deps = AdmissionDeps(profiles={"p": CPU_PROFILE}, caps={"p": 3},
                             stats=SYS_STATS)
        deps.register("p", _slow)
        queue = JobQueue(deps=deps, max_history=20, progress_poll_interval=0.01)
        await queue.start()
        try:
            ids = [await queue.submit("p") for _ in range(3)]
            for jid in ids:
                await queue.wait_for_job(jid, timeout=5.0)
            assert deps.max_active_by_instance["p"] >= 2, \
                f"cap=3 should allow same-instance co-run; got {deps.max_active_by_instance}"
        finally:
            await queue.stop()

    asyncio.run(scenario())


def test_no_profile_job_runs_exclusive():
    # A no-profile job never co-runs with anything (measurement run).
    async def scenario():
        deps = AdmissionDeps(profiles={"prof": CPU_PROFILE}, stats=SYS_STATS)
        deps.register("prof", _slow)
        deps.register("noprof", _slow)
        queue = JobQueue(deps=deps, max_history=20, progress_poll_interval=0.01)
        await queue.start()
        try:
            ids = [await queue.submit("noprof"), await queue.submit("prof"),
                   await queue.submit("prof")]
            for jid in ids:
                await queue.wait_for_job(jid, timeout=5.0)
            assert deps.max_active == 1, \
                f"no-profile job must run exclusive; max_active={deps.max_active}"
        finally:
            await queue.stop()

    asyncio.run(scenario())


GPU_6GB = {"gpu_memory_mb_peak_max": 6000.0, "memory_mb_peak_max": 100.0, "sample_count": 3}
GPU_2GB = {"gpu_memory_mb_peak_max": 2000.0, "memory_mb_peak_max": 100.0, "sample_count": 3}
GPU_STATS = {"gpu_free_memory_mb": 9500.0, "gpu_total_memory_mb": 10000.0,
             "memory_available_mb": 64000.0}


def test_gpu_ledger_serializes_over_budget():
    # Two 6GB-peak jobs on a 10GB total serialize (budget = 10000 * 0.9 = 9000).
    async def scenario():
        deps = AdmissionDeps(profiles={"g1": GPU_6GB, "g2": GPU_6GB}, stats=GPU_STATS)
        deps.register("g1", _slow)
        deps.register("g2", _slow)
        queue = JobQueue(deps=deps, max_history=20, progress_poll_interval=0.01)
        await queue.start()
        try:
            ids = [await queue.submit("g1"), await queue.submit("g2")]
            for jid in ids:
                await queue.wait_for_job(jid, timeout=5.0)
            assert deps.max_active == 1, \
                f"6GB+6GB exceeds the 9GB budget; max_active={deps.max_active}"
        finally:
            await queue.stop()

    asyncio.run(scenario())


def test_gpu_ledger_co_runs_within_budget():
    # A 6GB + 2GB pair fits the 9GB budget and co-runs.
    async def scenario():
        deps = AdmissionDeps(profiles={"g1": GPU_6GB, "g2": GPU_2GB}, stats=GPU_STATS)
        deps.register("g1", _slow)
        deps.register("g2", _slow)
        queue = JobQueue(deps=deps, max_history=20, progress_poll_interval=0.01)
        await queue.start()
        try:
            ids = [await queue.submit("g1"), await queue.submit("g2")]
            for jid in ids:
                await queue.wait_for_job(jid, timeout=5.0)
            assert deps.max_active == 2, \
                f"6GB+2GB fits the 9GB budget; max_active={deps.max_active}"
        finally:
            await queue.stop()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Stage 3: resource snapshots
# ---------------------------------------------------------------------------

def test_resource_snapshot_without_sysmon():
    worker = FakeWorkerProxy(pid=12345)
    queue = JobQueue(deps=ProxySysmonDeps(worker_proxy=worker), max_history=10)
    job = Job(id="j-1", capability_instance_id="plug-a", args=(), kwargs={})
    queue._jobs[job.id] = job
    snap = queue.get_resource_snapshot("j-1")
    assert snap is not None
    assert snap.worker_pid == 12345
    assert snap.cpu_percent == 42.0
    assert snap.memory_rss_mb == 512.0
    assert snap.gpu_index is None  # no sysmon -> GPU fields stay None
    assert snap.gpu_memory_mb is None
    assert snap.gpu_total_mb is None
    assert isinstance(snap.timestamp, datetime)


def test_resource_snapshot_with_sysmon():
    worker = FakeWorkerProxy(pid=12345)
    sysmon = FakeSysmon(worker_pid=12345)
    queue = JobQueue(deps=ProxySysmonDeps(worker_proxy=worker, sysmon=sysmon),
                     max_history=10, sysmon_capability_name='sysmon')
    job = Job(id="j-1", capability_instance_id="plug-a", args=(), kwargs={})
    queue._jobs[job.id] = job
    snap = queue.get_resource_snapshot("j-1")
    assert snap is not None
    assert snap.worker_pid == 12345
    assert snap.gpu_index == 0
    assert snap.gpu_memory_mb == 8500.0  # matched by worker_pid, not PID 99999
    assert snap.gpu_type == 'NVIDIA'
    assert snap.gpu_total_mb == 24000.0
    assert snap.gpu_load_percent == 78.0


def test_resource_snapshot_none_cases():
    class NoStatsProxy:
        pass

    queue = JobQueue(deps=ProxySysmonDeps(worker_proxy=NoStatsProxy()), max_history=10)
    job = Job(id="j-1", capability_instance_id="plug-a", args=(), kwargs={})
    queue._jobs[job.id] = job
    assert queue.get_resource_snapshot("j-1") is None  # proxy lacks get_stats
    assert queue.get_resource_snapshot("nonexistent") is None  # unknown job


def test_resource_snapshot_events_at_cadence():
    # RESOURCE_SNAPSHOT emitted at cadence during _poll_progress; also stored
    # on job.last_resource_snapshot.
    class ProgressProxy:
        def __init__(self, pid=99):
            self._stats = {'pid': pid, 'cpu_percent': 10.0, 'memory_rss_mb': 50.0}

        def get_stats(self):
            return self._stats

        def get_progress(self):
            return {'progress': 0.5, 'message': 'working'}

    async def scenario():
        queue = JobQueue(
            deps=ProxySysmonDeps(worker_proxy=ProgressProxy()), max_history=10,
            progress_poll_interval=0.01,       # fast polling for test
            resource_snapshot_cadence_polls=1,  # sample every poll
        )
        job = Job(id="j-5", capability_instance_id="plug-b", args=(), kwargs={})
        queue._jobs[job.id] = job

        received = []

        async def _collect_snapshots(gen, sink, limit):
            async for evt in gen:
                if evt.type == JobEventType.RESOURCE_SNAPSHOT:
                    sink.append(evt)
                    if len(sink) >= limit:
                        return

        sub = asyncio.create_task(
            _collect_snapshots(queue.events("j-5"), received, 2))
        poll_task = asyncio.create_task(queue._poll_progress(job, ProgressProxy()))
        try:
            await asyncio.wait_for(sub, timeout=2.0)
        finally:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

        assert len(received) == 2, \
            f"Expected 2 RESOURCE_SNAPSHOT events; got {len(received)}"
        snap_payload = received[0].payload.get("snapshot")
        assert snap_payload is not None
        assert snap_payload["worker_pid"] == 99
        assert snap_payload["cpu_percent"] == 10.0
        assert job.last_resource_snapshot is not None
        assert job.last_resource_snapshot.worker_pid == 99

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# CR-14 (stage 7): journal-primary emission, diagnostics, wedge gate
# ---------------------------------------------------------------------------

def test_journal_primary_emission_and_durable_history(tmp_path):
    async def scenario():
        journal = LocalJournalStore(tmp_path / "journal.db")
        diagnostics = LocalDiagnosticsStore(tmp_path / "diag.db")
        deps = ProxySysmonDeps(worker_proxy=FakeWorkerProxy())
        queue = JobQueue(deps=deps, max_history=10,
                         journal=journal, diagnostics=diagnostics,
                         progress_poll_interval=0.01)

        # Journal-primary emission + class routing: a real job run produces
        # durable rows for journal-class events; liveness types never land.
        await queue.start()
        try:
            jid = await queue.submit("plug-a", "hello")
            done = await queue.wait_for_job(jid, timeout=5.0)
            assert done.status == JobStatus.completed
        finally:
            await queue.stop()

        rows = journal.query(job_id=jid)
        types = [r.event_type for r in rows]
        assert types.count("state_transition") == 2, types  # running + completed
        assert all(t not in ("progress_changed", "resource_snapshot") for t in types), \
            f"liveness events must never be journaled: {types}"

        # Terminal row carries the job snapshot; rehydrated durable history
        # matches (the `_history` migration rider).
        terminal = journal.terminal_state_events()
        assert len(terminal) == 1
        snap = terminal[0].payload["job_snapshot"]
        assert snap["id"] == jid and snap["status"] == "completed"
        hist = queue.get_history_from_journal()
        assert len(hist) == 1 and hist[0].id == jid
        assert hist[0].status == JobStatus.completed
        assert hist[0].started_at is not None and hist[0].completed_at is not None

        # EXACT per-job diagnostics (replaces the deleted timestamp-window
        # slicer).
        diagnostics.append_record(DiagnosticRecord(
            message="model loading", job_id=jid, worker_session_id="ws-x"))
        diagnostics.append_record(DiagnosticRecord(
            message="other job", job_id="other", worker_session_id="ws-x"))
        mine = queue.get_job_diagnostics(jid)
        assert len(mine) == 1 and mine[0].message == "model loading"

    asyncio.run(scenario())


def test_wedge_gate_refuses_new_submissions():
    # A journal whose append raises wedges the queue; the NEXT submit refuses
    # loudly (never silent audit loss, never a raise mid-finalization).
    class WedgedJournal:
        def append(self, event):
            raise RuntimeError("disk full")

    async def scenario():
        deps = ProxySysmonDeps(worker_proxy=FakeWorkerProxy())
        queue = JobQueue(deps=deps, max_history=10, journal=WedgedJournal())
        job = Job(id="jw", capability_instance_id="p", args=(), kwargs={})
        job.status = JobStatus.running
        queue._jobs[job.id] = job
        queue._emit_state_transition(job, JobStatus.pending)  # ERROR-logged, no raise
        assert queue._journal_wedged is True
        with pytest.raises(RuntimeError, match="WEDGED"):
            await queue.submit("p")

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# CR-14 follow-up: run_id/actor correlation + ADMISSION_DECIDED
# ---------------------------------------------------------------------------

class RunCorrelationDeps:
    """Driver fake that also captures the call envelope the queue set."""
    def __init__(self):
        self.seen_envelopes = []

    def get_capability_meta(self, name_or_id):
        return None

    def get_capability(self, name_or_id):
        return object()

    async def execute_capability_async(self, name_or_id, *args, **kwargs):
        self.seen_envelopes.append(get_call_envelope())
        return {"ok": True, "got_kwargs": dict(kwargs)}

    async def execute_capability_task_async(self, name_or_id, task_name, method, **kwargs):
        self.seen_envelopes.append(get_call_envelope())
        return {"ok": True}

    def reload_capability(self, name_or_id):
        return None


def test_run_correlation_threading(tmp_path):
    async def scenario():
        journal = LocalJournalStore(tmp_path / "journal.db")
        deps = RunCorrelationDeps()
        queue = JobQueue(deps=deps, max_history=10, journal=journal,
                         progress_poll_interval=0.01)
        await queue.start()
        try:
            # 1. submit(run_id=, actor=): reserved kwargs never reach the
            # capability; every journal row for the job carries both tags.
            jid = await queue.submit("plug-a", payload="x",
                                     run_id="run_test_001", actor="cli:tester")
            done = await queue.wait_for_job(jid, timeout=5.0)
            assert done.status == JobStatus.completed
            assert done.result["got_kwargs"] == {"payload": "x"}, \
                "run_id/actor must not leak into capability kwargs"

            rows = journal.query(job_id=jid)
            assert rows, "expected journal rows for the job"
            assert all(r.run_id == "run_test_001" for r in rows), \
                [(r.event_type, r.run_id) for r in rows]
            assert all(r.actor == "cli:tester" for r in rows)

            # run_id is a first-class query filter.
            by_run = journal.query(run_id="run_test_001")
            assert {r.job_id for r in by_run} == {jid}

            # 2. The call envelope carried run_id/actor to the execute path.
            env = deps.seen_envelopes[0]
            assert env is not None and env.run_id == "run_test_001"
            assert env.actor == "cli:tester"

            # 3. ADMISSION_DECIDED: one row per admit, with decision detail.
            adm = journal.query(job_id=jid,
                                event_type=SubstrateEventType.ADMISSION_DECIDED.value)
            assert len(adm) == 1, [r.event_type for r in journal.query(job_id=jid)]
            assert adm[0].payload["exclusive"] is True  # no empirical profile
            assert adm[0].run_id == "run_test_001"

            # 4. Terminal snapshot round-trip: rehydrated history carries tags.
            hist = queue.get_history_from_journal()
            assert hist[0].run_id == "run_test_001"
            assert hist[0].actor == "cli:tester"

            # 5. Composition members inherit Composition.run_id/actor.
            comp_id = await queue.submit_composition(Composition(
                nodes=[CompositionNode("n1", "plug-b", {"k": "v"})],
                run_id="run_test_002", actor="cli:tester",
            ))
            run = await queue.wait_for_composition(comp_id, timeout=5.0)
            assert run.status == NodeState.completed
            member_jid = run.node_runs["n1"].job_id
            mrows = journal.query(job_id=member_jid)
            assert mrows and all(r.run_id == "run_test_002" for r in mrows), \
                [(r.event_type, r.run_id) for r in mrows]

            # 6. Envelope-less submits stay honestly untagged.
            jid3 = await queue.submit("plug-c")
            await queue.wait_for_job(jid3, timeout=5.0)
            rows3 = journal.query(job_id=jid3)
            assert all(r.run_id is None and r.actor is None for r in rows3)

            # 7. Queue-scoped run context (set_run_context): defaults apply
            # when no explicit tags are passed; explicit overrides win.
            queue.set_run_context(run_id="run_ctx_003", actor="cli:ctx")
            jid4 = await queue.submit("plug-d")
            await queue.wait_for_job(jid4, timeout=5.0)
            rows4 = journal.query(job_id=jid4)
            assert rows4 and all(r.run_id == "run_ctx_003" and r.actor == "cli:ctx"
                                 for r in rows4), \
                [(r.event_type, r.run_id) for r in rows4]
            jid5 = await queue.submit("plug-e", run_id="run_override")
            await queue.wait_for_job(jid5, timeout=5.0)
            rows5 = journal.query(job_id=jid5)
            assert rows5 and all(r.run_id == "run_override" for r in rows5)
            queue.set_run_context()  # clear
        finally:
            await queue.stop()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# CR-6 stage 4: cancel phases, retry observer, block reason
# ---------------------------------------------------------------------------

class FakeCancelCapability:
    """Worker proxy fake with cooperative cancel + slow execute."""
    def __init__(self, ack_cooperative: bool = True, exec_delay: float = 5.0):
        self._ack = ack_cooperative
        self._exec_delay = exec_delay
        self._cancel_event = asyncio.Event()

    async def cancel_async(self):
        if self._ack:
            self._cancel_event.set()

    def get_stats(self):
        return {'pid': 1234, 'cpu_percent': 5.0, 'memory_rss_mb': 10.0}


class Stage4Deps:
    """Driver fake; exposes the _on_retry attribute slot used by start()/stop()
    and the queue's retry observer."""
    def __init__(self, capability, ack_cooperative: bool = True,
                 raise_resource_n_times: int = 0):
        self._capability = capability
        self._ack = ack_cooperative
        self._resource_raises_remaining = raise_resource_n_times
        self.reload_count = 0
        self._on_retry = None  # observer slot; queue.start() overwrites this

    def get_capability_meta(self, name_or_id):
        return FakeMeta()

    def get_capability(self, name_or_id):
        return self._capability

    async def execute_capability_async(self, name_or_id, *args, **kwargs):
        # Simulate CR-7's reactive-retry loop: raise CapabilityResourceError
        # N times, invoke the _on_retry observer before each retry, succeed.
        for attempt in range(self._resource_raises_remaining + 1):
            if attempt > 0 and self._on_retry is not None:
                # Mimic CapabilityManager's invocation right before the retry
                self._on_retry(name_or_id, attempt, CapabilityResourceError("simulated"))
            if attempt < self._resource_raises_remaining:
                continue  # skip to next iteration (simulating raise+catch+retry)
            # Final iteration — actually run the cooperative-cancel logic.
            if self._ack:
                done, pending = await asyncio.wait(
                    [asyncio.create_task(self._capability._cancel_event.wait()),
                     asyncio.create_task(asyncio.sleep(self._capability._exec_delay))],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                return "cooperative-cancel-result"
            else:
                await asyncio.sleep(self._capability._exec_delay)
                return "should-not-reach"

    def reload_capability(self, name_or_id):
        self.reload_count += 1


async def _wait_until_running(queue, job_id):
    for _ in range(40):
        if any(j.id == job_id for j in queue.get_running_jobs()):
            return
        await asyncio.sleep(0.05)


async def _collect_phases(gen, sink):
    async for evt in gen:
        if evt.type == JobEventType.CANCEL_PHASE_CHANGED:
            sink.append(evt.payload.get("to"))
            if evt.payload.get("to") == "completed":
                return


def test_cancel_cooperative_success_phases():
    # Cooperative-success path: COOPERATIVE -> COMPLETED, no worker reload.
    async def scenario():
        capability = FakeCancelCapability(ack_cooperative=True, exec_delay=5.0)
        deps = Stage4Deps(capability, ack_cooperative=True)
        queue = JobQueue(deps=deps, max_history=10, cancel_timeout=0.5,
                         progress_poll_interval=0.05)
        await queue.start()
        try:
            job_id = await queue.submit("plug-x")
            await _wait_until_running(queue, job_id)

            phases = []
            collector = asyncio.create_task(
                _collect_phases(queue.events(job_id), phases))
            await asyncio.sleep(0.05)
            await queue.cancel(job_id)
            await asyncio.wait_for(collector, timeout=5.0)

            assert phases == ["cooperative", "completed"], \
                f"Cooperative path: expected [cooperative, completed]; got {phases}"
            assert deps.reload_count == 0, \
                "Cooperative-success path must NOT reload the worker"
            assert queue.get_job(job_id).cancel_phase == CancelPhase.COMPLETED
        finally:
            await queue.stop()

    asyncio.run(scenario())


def test_cancel_force_kill_phases():
    # Force-kill path: COOPERATIVE -> FORCE -> RELOADING -> COMPLETED.
    async def scenario():
        capability = FakeCancelCapability(ack_cooperative=False, exec_delay=10.0)
        deps = Stage4Deps(capability, ack_cooperative=False)
        queue = JobQueue(deps=deps, max_history=10, cancel_timeout=0.2,
                         progress_poll_interval=0.05)
        await queue.start()
        try:
            job_id = await queue.submit("plug-y")
            await _wait_until_running(queue, job_id)

            phases = []
            collector = asyncio.create_task(
                _collect_phases(queue.events(job_id), phases))
            await asyncio.sleep(0.05)
            await queue.cancel(job_id)
            await asyncio.wait_for(collector, timeout=5.0)

            assert phases == ["cooperative", "force", "reloading", "completed"], \
                f"Force-kill path: expected full sequence; got {phases}"
            assert deps.reload_count == 1, \
                f"Force-kill must reload worker once; got {deps.reload_count}"

            job = queue.get_job(job_id)
            assert job.cancel_phase == CancelPhase.COMPLETED
            assert job.cancel_requested_at is not None
        finally:
            await queue.stop()

    asyncio.run(scenario())


def test_retry_started_events():
    # Deps simulates CapabilityResourceError twice; the observer fires per retry.
    async def scenario():
        capability = FakeCancelCapability(ack_cooperative=True, exec_delay=0.1)
        deps = Stage4Deps(capability, ack_cooperative=True, raise_resource_n_times=2)
        queue = JobQueue(deps=deps, max_history=10, progress_poll_interval=0.05)
        await queue.start()
        try:
            retries = []
            collector_done = asyncio.Event()

            async def _collect_retries(gen, sink, target_count):
                async for evt in gen:
                    if evt.type == JobEventType.RETRY_STARTED:
                        sink.append(evt)
                        if len(sink) >= target_count:
                            collector_done.set()
                            return

            # Subscribe to all_events so we catch retries on any job_id.
            sub = asyncio.create_task(
                _collect_retries(queue.all_events(), retries, 2))
            await asyncio.sleep(0.05)  # let subscription register

            job_id = await queue.submit("plug-z")
            # Simulated retries fire from inside execute_capability_async; the
            # job completes after 3 iterations (2 retries + 1 success).
            try:
                await asyncio.wait_for(collector_done.wait(), timeout=5.0)
            finally:
                sub.cancel()
                try:
                    await sub
                except asyncio.CancelledError:
                    pass

            assert len(retries) == 2, \
                f"Expected 2 RETRY_STARTED events; got {len(retries)}"
            assert retries[0].payload["attempt"] == 1
            assert retries[1].payload["attempt"] == 2
            assert retries[0].job_id == job_id, \
                "RETRY_STARTED should be tagged with the in-flight job"

            await queue.wait_for_job(job_id, timeout=5.0)
            final_job = queue.get_job(job_id)
            assert final_job.retry_count == 2, \
                f"Job.retry_count should be 2; got {final_job.retry_count}"
            assert final_job.status == JobStatus.completed
        finally:
            await queue.stop()

    asyncio.run(scenario())


def test_block_reason_events_dedupe():
    async def scenario():
        queue = JobQueue(deps=Stage4Deps(FakeCancelCapability()), max_history=10)
        job = Job(id="j-4", capability_instance_id="plug-q", args=(), kwargs={})
        queue._jobs[job.id] = job
        received = []

        async def _collect_block(gen, sink):
            async for evt in gen:
                if evt.type == JobEventType.BLOCK_REASON_CHANGED:
                    sink.append(evt)
                    if len(sink) >= 2:
                        return

        sub = asyncio.create_task(_collect_block(queue.events("j-4"), received))
        await asyncio.sleep(0.02)
        queue._emit_block_reason(job, "Worker busy")
        queue._emit_block_reason(job, "Worker busy")  # no-op (same reason)
        queue._emit_block_reason(job, "GPU unavailable")
        await asyncio.wait_for(sub, timeout=2.0)

        assert len(received) == 2, "Repeated same-reason calls should dedupe"
        assert received[0].payload["to"] == "Worker busy"
        assert received[0].payload["from"] is None
        assert received[1].payload["from"] == "Worker busy"
        assert received[1].payload["to"] == "GPU unavailable"
        assert job.block_reason == "GPU unavailable"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Stage 4 (CR-17 pt 2): task-channel routing
# ---------------------------------------------------------------------------

class TaskChannelDeps:
    def __init__(self):
        self.task_calls = []
        self.exec_calls = []

    def get_capability_meta(self, name_or_id):
        return None

    def get_capability(self, name_or_id):
        return object()  # non-None: loaded-check passes; no cancel paths used

    async def execute_capability_async(self, name_or_id, *args, **kwargs):
        self.exec_calls.append((name_or_id, args, kwargs))
        return {"channel": "execute"}

    async def execute_capability_task_async(self, name_or_id, task_name, method, **kwargs):
        self.task_calls.append((name_or_id, task_name, method, kwargs))
        return {"channel": "task"}

    def reload_capability(self, name_or_id):
        pass


def test_task_channel_routing():
    async def scenario():
        deps = TaskChannelDeps()
        queue = JobQueue(deps)
        await queue.start()
        try:
            jid = await queue.submit("graph", task="graph-storage",
                                     method="query_nodes",
                                     query={"type": "node_query"})
            job = await queue.wait_for_job(jid, timeout=10)
            assert job.status == JobStatus.completed, job.error
            assert job.result == {"channel": "task"}
            assert deps.task_calls == [
                ("graph", "graph-storage", "query_nodes",
                 {"query": {"type": "node_query"}})]
            # Execute channel unchanged.
            jid2 = await queue.submit("graph", "posarg", key="val")
            job2 = await queue.wait_for_job(jid2, timeout=10)
            assert job2.result == {"channel": "execute"}
            assert deps.exec_calls == [("graph", ("posarg",), {"key": "val"})]
            # Validation: task without method refuses at submit.
            with pytest.raises(CapabilityInputError):
                await queue.submit("graph", task="graph-storage")
        finally:
            await queue.stop()

    asyncio.run(scenario())
