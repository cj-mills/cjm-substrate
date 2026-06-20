#!/usr/bin/env python3
"""Stage-7 follow-up stress suite — the CR-14 observability record architecture.

Ratified 6-item list (stage-7 ledger §"Proposed stress list"); this script
covers items 1–5 synthetically; item 6 (the real-corpus volume regression)
lives in `cjm-transcript-decomp-core/tests_manual/validate_stage7_volume_journal_e2e.py`.

  1. Journal-write latency on the dispatch fast path (G4 no-awaits
     constraint) at stage-3 loop-back volume (432 jobs).
  2. Wedged journal → loud failure (ERROR + submit refusal), never silent
     drop, never corrupted in-flight state.
  3. kill -9 mid-write on BOTH DBs (WAL recovery) + the death-rattle pump
     floor (a SIGKILLed writer's final stdout lines are present). The
     real-worker CR-7 kill-9 arc was stress-validated at stage 4
     (`cjm-graph-plugin-sqlite/tests_manual/validate_stage4_graph_storage_e2e.py`).
  4. Retention DELETE racing active diagnostic writers.
  5. Late-subscriber catch-up exactness ACROSS a host restart (journal
     cursor replay parity with a continuously-connected subscriber).

Run (substrate dev env): conda run -n cjm-substrate python tests_manual/validate_stage7_observability_e2e.py

As-measured baselines (2026-06-12, 9950X): part 1 — 432 jobs journaled in
11.1s vs 10.9s unjournaled (1.02x; 1,296 rows = 432×(2 transitions +
1 admission)); mean LocalJournalStore.append 0.27 ms. THE PART-1 CATCH:
the original per-call-connection store shape measured 17.2 ms/append (the
close ran a WAL checkpoint every time) — 25x over the ratified "µs-scale
synchronous INSERTs" claim; fixed by the persistent lock-protected
connection + synchronous=NORMAL in both stores.

Per [[stage-stress-suites]]: this script persists NO config (no I8 exposure);
all stores live in a TemporaryDirectory.
"""
import asyncio
import json
import logging
import os
import signal
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from cjm_substrate.core.diagnostics_store import (
    DiagnosticRecord, LocalDiagnosticsStore, StreamChunk,
)
from cjm_substrate.core.journal_store import (
    JournalEvent, LocalJournalStore, SubstrateEventType,
)
from cjm_substrate.core.proxy import _pump_stream
from cjm_substrate.core.queue import JobEventType, JobQueue, JobStatus

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("stage7-stress")

_CPU = {"gpu_memory_mb_peak_max": 0.0, "memory_mb_peak_max": 10.0, "sample_count": 5}
_STATS = {"gpu_free_memory_mb": 20000.0, "gpu_total_memory_mb": 24000.0,
          "memory_available_mb": 64000.0}


@dataclass
class _Meta:
    enabled: bool = True


class _FastDeps:
    """Near-instant fake deps with the stage-3 admission surface (profiled
    instances co-run; per-instance cap 1 serializes same-instance jobs)."""

    def __init__(self, instances):
        self._instances = set(instances)

    def get_capability_meta(self, name_or_id):
        return _Meta()

    def get_capability(self, name_or_id):
        return object()  # no get_progress/get_stats → no liveness polling

    async def execute_capability_async(self, name_or_id, *args, **kwargs):
        await asyncio.sleep(0)
        return {"ok": True}

    async def execute_capability_task_async(self, name_or_id, task, method, **kwargs):
        await asyncio.sleep(0)
        return {"ok": True}

    def reload_capability(self, name_or_id):
        return None

    def get_admission_profile(self, name_or_id):
        return dict(_CPU)

    def get_instance_concurrency_cap(self, name_or_id):
        return None

    async def get_global_stats(self):
        return dict(_STATS)


# ---------------------------------------------------------------------------
# Part 1 — dispatch fast-path latency at 432-job volume
# ---------------------------------------------------------------------------
async def _run_volume(journal, n_jobs=432, n_instances=8):
    deps = _FastDeps([f"inst-{i}" for i in range(n_instances)])
    q = JobQueue(deps=deps, max_history=n_jobs + 1, journal=journal,
                 progress_poll_interval=5.0)
    q.set_run_context(run_id="stress7-volume", actor="stress:part1")
    await q.start()
    t0 = time.monotonic()
    ids = [await q.submit(f"inst-{i % n_instances}", payload=i) for i in range(n_jobs)]
    for jid in ids:
        job = await q.wait_for_job(jid, timeout=120.0)
        assert job.status == JobStatus.completed, (jid, job.status, job.error)
    wall = time.monotonic() - t0
    await q.stop()
    return wall


def part1_latency(tmp: Path):
    log.info("=== Part 1: dispatch fast-path latency at 432-job volume ===")
    journal = LocalJournalStore(tmp / "p1-journal.db")

    # Direct append micro-benchmark (the synchronous-tiny-WAL-INSERT claim).
    times = []
    for i in range(300):
        ev = JournalEvent(event_type="state_transition", job_id=f"bench-{i}",
                          payload={"from": "a", "to": "b"})
        t0 = time.perf_counter()
        journal.append(ev)
        times.append(time.perf_counter() - t0)
    mean_ms = statistics.mean(times) * 1000
    p95_ms = sorted(times)[int(len(times) * 0.95)] * 1000
    log.info(f"append latency: mean {mean_ms:.2f} ms, p95 {p95_ms:.2f} ms")
    assert mean_ms < 10.0, f"mean append {mean_ms:.2f} ms exceeds the 10 ms budget"

    wall_off = asyncio.run(_run_volume(journal=None))
    journal2 = LocalJournalStore(tmp / "p1-journal2.db")
    wall_on = asyncio.run(_run_volume(journal=journal2))
    log.info(f"432-job loop: unjournaled {wall_off:.2f}s vs journaled {wall_on:.2f}s "
             f"({wall_on / max(wall_off, 1e-9):.2f}x)")

    rows = journal2.count()
    st = journal2.count("state_transition")
    adm = journal2.count(SubstrateEventType.ADMISSION_DECIDED.value)
    assert st == 432 * 2, f"expected 864 state transitions, got {st}"
    assert adm == 432, f"expected 432 admission rows, got {adm}"
    assert rows == st + adm, f"unexpected extra rows: {rows} != {st + adm}"
    # Every row carries the run context (item-3 threading at volume).
    with sqlite3.connect(tmp / "p1-journal2.db") as con:
        missing = con.execute(
            "SELECT COUNT(*) FROM journal WHERE run_id != 'stress7-volume'").fetchone()[0]
    assert missing == 0, f"{missing} rows missing the run context"
    # Generous bound: journaling must not blow up the dispatch path.
    assert wall_on < wall_off * 3 + 2.0, (
        f"journaled volume run {wall_on:.2f}s vs {wall_off:.2f}s — over budget")
    log.info(f"part 1 PASS ({rows} rows, all run-tagged)")


# ---------------------------------------------------------------------------
# Part 2 — wedged journal: loud, never silent, never corrupting
# ---------------------------------------------------------------------------
def part2_wedge(tmp: Path):
    log.info("=== Part 2: wedged journal → loud failure ===")
    ro_dir = tmp / "readonly"
    ro_dir.mkdir()
    # Pre-create the DB file OUTSIDE the store (the store's persistent
    # connection would otherwise hold a writable fd across the chmod and
    # defeat the test setup), then make it read-only: the store's first
    # connection fails at the WAL pragma -> append raises -> wedge.
    con = sqlite3.connect(ro_dir / "journal.db")
    con.execute("CREATE TABLE placeholder (i INTEGER)")
    con.commit()
    con.close()
    os.chmod(ro_dir / "journal.db", 0o444)
    os.chmod(ro_dir, 0o555)
    journal = LocalJournalStore(ro_dir / "journal.db")

    errors = []

    class _Capture(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                errors.append(record.getMessage())

    handler = _Capture()
    logging.getLogger().addHandler(handler)
    try:
        async def _go():
            deps = _FastDeps(["inst-0"])
            q = JobQueue(deps=deps, max_history=10, journal=journal,
                         progress_poll_interval=5.0)
            await q.start()
            try:
                # The in-flight job COMPLETES (its result is never sacrificed
                # to the journal failure)…
                jid = await q.submit("inst-0")
                job = await q.wait_for_job(jid, timeout=30.0)
                assert job.status == JobStatus.completed, job.status
                assert q._journal_wedged is True, "queue must wedge on append failure"
                # …and the NEXT submit refuses loudly.
                try:
                    await q.submit("inst-0")
                    raise AssertionError("wedged queue accepted a submission")
                except RuntimeError as e:
                    assert "WEDGED" in str(e), e
            finally:
                await q.stop()

        asyncio.run(_go())
    finally:
        logging.getLogger().removeHandler(handler)
        os.chmod(ro_dir, 0o755)
        os.chmod(ro_dir / "journal.db", 0o644)
    assert any("JOURNAL APPEND FAILED" in m for m in errors), (
        f"expected an ERROR-level journal failure log; got {errors[:3]}")
    log.info("part 2 PASS (in-flight job kept its result; ERROR logged; submit refused)")


# ---------------------------------------------------------------------------
# Part 3 — kill -9 mid-write on both DBs + the death-rattle pump floor
# ---------------------------------------------------------------------------
_WRITER_PROG = r"""
import sys, time
from pathlib import Path
from cjm_substrate.core.diagnostics_store import (
    DiagnosticRecord, LocalDiagnosticsStore)
from cjm_substrate.core.journal_store import JournalEvent, LocalJournalStore

d = LocalDiagnosticsStore(Path(sys.argv[1]))
j = LocalJournalStore(Path(sys.argv[2]))
i = 0
while True:
    d.append_record(DiagnosticRecord(message=f"spin {i}", worker_session_id="kill9"))
    j.append(JournalEvent(event_type="state_transition", job_id=f"kill9-{i}"))
    print(f"alive {i}", flush=True)
    i += 1
"""


def part3_kill9(tmp: Path):
    log.info("=== Part 3: kill -9 mid-write + death-rattle floor ===")
    diag_db = tmp / "p3-diag.db"
    jour_db = tmp / "p3-journal.db"
    proc = subprocess.Popen(
        [sys.executable, "-c", _WRITER_PROG, str(diag_db), str(jour_db)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    # The host pump captures the writer's stdout exactly as the proxy does
    # for a worker — the zero-cooperation floor under test.
    pump_store = LocalDiagnosticsStore(tmp / "p3-pump.db")
    pump = threading.Thread(target=_pump_stream,
                            args=(proc.stdout, pump_store, "session-kill9"),
                            daemon=True)
    pump.start()
    time.sleep(1.5)  # let it write mid-stride
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=10)
    pump.join(timeout=10)
    assert proc.returncode == -signal.SIGKILL

    # Both DBs recover (WAL) and are queryable.
    d = LocalDiagnosticsStore(diag_db)
    recs = d.query_records(worker_session_id="kill9")
    j = LocalJournalStore(jour_db)
    rows = j.query(limit=None)
    for db in (diag_db, jour_db):
        with sqlite3.connect(db) as con:
            ok = con.execute("PRAGMA quick_check").fetchone()[0]
        assert ok == "ok", f"{db.name}: quick_check={ok}"
    assert len(recs) > 0 and len(rows) > 0, (len(recs), len(rows))
    # Post-kill appends still work (no lock/wedge residue).
    d.append_record(DiagnosticRecord(message="post-kill", worker_session_id="kill9"))
    j.append(JournalEvent(event_type="post_kill_probe"))

    # Death rattle: the killed process's final stdout lines are present.
    chunks = pump_store.query_chunks(worker_session_id="session-kill9")
    assert chunks, "pump captured nothing before the SIGKILL"
    last = chunks[-1].content
    assert last.startswith("alive "), last
    log.info(f"part 3 PASS (diag {len(recs)} rows, journal {len(rows)} rows, "
             f"quick_check ok ×2; death rattle: {len(chunks)} chunks, last={last!r})")


# ---------------------------------------------------------------------------
# Part 4 — retention DELETE racing active writers
# ---------------------------------------------------------------------------
def part4_retention_race(tmp: Path):
    log.info("=== Part 4: retention DELETE vs active writers ===")
    store = LocalDiagnosticsStore(tmp / "p4-diag.db")
    failures = []
    stop = threading.Event()

    def writer(wid):
        try:
            i = 0
            while not stop.is_set():
                store.append_record(DiagnosticRecord(
                    message=f"w{wid}-{i}", worker_session_id=f"ws-{wid}"))
                store.append_chunk(StreamChunk(
                    content=f"chunk w{wid}-{i}", worker_session_id=f"ws-{wid}"))
                i += 1
        except Exception as e:
            failures.append(f"writer {wid}: {e!r}")

    threads = [threading.Thread(target=writer, args=(w,), daemon=True)
               for w in range(4)]
    for t in threads:
        t.start()
    deleted_total = 0
    try:
        for _ in range(20):
            time.sleep(0.05)
            out = store.apply_retention(max_age_days=1e-9)  # everything is "old"
            deleted_total += out["records_deleted"] + out["chunks_deleted"]
    except Exception as e:
        failures.append(f"retention: {e!r}")
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=10)
    assert not failures, failures
    assert deleted_total > 0, "retention never deleted anything during the race"
    with sqlite3.connect(tmp / "p4-diag.db") as con:
        ok = con.execute("PRAGMA quick_check").fetchone()[0]
    assert ok == "ok", ok
    # Store remains fully usable.
    store.append_record(DiagnosticRecord(message="post-race"))
    assert store.query_records(limit=5)
    log.info(f"part 4 PASS ({deleted_total} rows deleted under contention; quick_check ok)")


# ---------------------------------------------------------------------------
# Part 5 — late-subscriber catch-up exactness across a host restart
# ---------------------------------------------------------------------------
def part5_late_subscriber(tmp: Path):
    log.info("=== Part 5: late-subscriber catch-up across restart ===")
    jpath = tmp / "p5-journal.db"

    async def _session(run_tag, n_jobs, collect):
        journal = LocalJournalStore(jpath)
        deps = _FastDeps(["inst-0", "inst-1"])
        q = JobQueue(deps=deps, max_history=n_jobs + 1, journal=journal,
                     progress_poll_interval=5.0)
        q.set_run_context(run_id=run_tag, actor="stress:part5")
        await q.start()
        seen = []

        async def _collector():
            async for ev in q.all_events():
                seen.append(ev)

        task = asyncio.create_task(_collector())
        await asyncio.sleep(0.05)
        ids = [await q.submit(f"inst-{i % 2}", payload=i) for i in range(n_jobs)]
        for jid in ids:
            await q.wait_for_job(jid, timeout=60.0)
        await asyncio.sleep(0.2)  # drain the bus
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await q.stop()
        collect.extend(seen)

    live_a, live_b = [], []
    asyncio.run(_session("p5-run-1", 10, live_a))   # subscriber A: session 1 only
    asyncio.run(_session("p5-run-2", 10, live_b))   # host restart; subscriber B live for session 2

    # The late subscriber arrives AFTER everything: full replay from cursor 0.
    journal = LocalJournalStore(jpath)
    replay = journal.query(after_seq=0, limit=None)

    def keyset(events):
        # journal-class job events only (liveness never persists)
        return [(e.type.value if hasattr(e, "type") else e.event_type, e.job_id)
                for e in events
                if (e.type.value if hasattr(e, "type") else e.event_type)
                not in ("progress_changed", "resource_snapshot")]

    live_keys = keyset(live_a) + keyset(live_b)
    # The replay additionally holds ADMISSION_DECIDED rows (pure journal, no
    # bus fan-out) — exactness claim: replay ⊇ live, and the job-event
    # subsequence matches the live order EXACTLY.
    replay_jobevents = [(e.event_type, e.job_id) for e in replay
                        if e.event_type in ("state_transition",)]
    live_jobevents = [k for k in live_keys if k[0] == "state_transition"]
    assert replay_jobevents == live_jobevents, (
        f"replay/live divergence: {len(replay_jobevents)} vs {len(live_jobevents)}")
    runs = {e.run_id for e in replay}
    assert runs == {"p5-run-1", "p5-run-2"}, runs
    adm = [e for e in replay if e.event_type == SubstrateEventType.ADMISSION_DECIDED.value]
    assert len(adm) == 20, len(adm)
    # Cursor semantics: replay from a mid-point yields exactly the tail.
    mid = replay[len(replay) // 2].seq
    tail = journal.query(after_seq=mid, limit=None)
    assert [e.event_id for e in tail] == [e.event_id for e in replay if e.seq > mid]
    log.info(f"part 5 PASS (replay {len(replay)} rows == live across restart; "
             f"cursor tail exact)")


def main():
    with tempfile.TemporaryDirectory(prefix="stage7-stress-") as td:
        tmp = Path(td)
        part1_latency(tmp)
        part2_wedge(tmp)
        part3_kill9(tmp)
        part4_retention_race(tmp)
        part5_late_subscriber(tmp)
    log.info("=== stage-7 observability stress suite: ALL PASS ===")


if __name__ == "__main__":
    main()
