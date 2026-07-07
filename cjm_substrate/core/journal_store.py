"""CR-14 (stage 7): the durable account-of-action. One substrate-derived,
host-written, never-auto-deleted SQLite store of typed observability events —
the operational half of the attempted-vs-happened asymmetry (the graph records
what HAPPENED; the journal records what was ATTEMPTED, including everything
the graph by design refuses to contain: failures, refusals, retries, admission
decisions, worker lifecycle). Design ledger: claude-docs/stage-7-evidence.md."""

import json
import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Protocol, runtime_checkable

from fastcore.basics import patch

_logger = logging.getLogger(__name__)


class SubstrateEventType(str, Enum):
    """Journal vocabulary beyond the job-scoped `JobEventType` set (CR-14).

    Reserved up front (emission progressive). Job-scoped types stay in
    `core.queue.JobEventType`; both serialize to plain strings in the
    journal's `event_type` column — the journal is vocabulary-tolerant
    by design (unknown types round-trip; the P5/P6 tolerant-unknown law).
    """
    WORKER_SPAWNED = "worker_spawned"      # Worker subprocess launched (proxy)
    WORKER_READY = "worker_ready"          # Worker passed readiness probe (proxy)
    WORKER_DIED = "worker_died"            # Worker exited / was terminated (proxy cleanup or death check)
    ADAPTER_BOUND = "adapter_bound"        # Host-matched adapter impls bound at spawn (CR-17 pt 2)
    ADMISSION_DECIDED = "admission_decided"  # Stage-3 admission outcome for a dispatch (reserved; emission is a follow-up)
    CONFIG_APPLIED = "config_applied"      # Effective config (hash) applied at load/reconfigure
    TASK_ACCOUNT = "task_account"          # In-worker adapter task account, rides wire metadata (reserved)
    RESULT_SAVED = "result_saved"          # Storage-helper save (T29) account (reserved)
    CACHE_HIT = "cache_hit"                # Storage-helper cache hit account (reserved)
    GRAPH_EXTENDED = "graph_extended"      # Graph mutation account from the storage adapter (reserved)
    RUN_STARTED = "run_started"            # Host-tier: a core run began (links run manifests to the journal)
    RUN_FINISHED = "run_finished"          # Host-tier: a core run ended
    VERIFY_OUTCOME = "verify_outcome"      # Host-tier: a core's skeptical-lens verify result (I14: outcomes are rows, not log lines)


# Class routing for the single emission path (CR-14 ratified design #8):
# journal-class events -> journal row + bus notify; liveness-class -> notify
# only (final values ride the terminal STATE_TRANSITION row's payload).
# Values are the raw JobEventType strings so this module never imports the
# queue (dependency direction: queue -> journal_store).
LIVENESS_EVENT_TYPES: frozenset = frozenset({
    "progress_changed",   # JobEventType.PROGRESS_CHANGED
    "resource_snapshot",  # JobEventType.RESOURCE_SNAPSHOT
})


@dataclass
class JournalEvent:
    """One durable observability record (CR-14).

    The journal never duplicates what manifests / capability DBs / the graph
    already record — graph-touching payloads carry REFERENCES (node ids +
    content hashes, verifiable via the CR-19 machinery), never content.
    `worker_reported=True` marks payloads that originated in-worker and rode
    a wire envelope; the HOST still wrote the row (single-writer-class rule).
    `event_id` is GENERATED, not derived — events are occurrences (the
    stage-5 identity rule's asserted class); `EventRef(event_id)` from
    cjm-context-graph-primitives anchors graph->journal references.
    """
    event_type: str  # JobEventType.value or SubstrateEventType.value (vocabulary-tolerant)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))  # Generated occurrence id (EventRef anchor)
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))  # Substrate-stamped, tz-aware UTC
    run_id: Optional[str] = None  # Host-tier run correlation (core run manifests)
    job_id: Optional[str] = None  # Queue job correlation
    composition_id: Optional[str] = None  # Stage-3 composition correlation
    node_id: Optional[str] = None  # Composition node correlation
    capability_instance_id: Optional[str] = None  # CR-10 instance correlation
    capability_name: Optional[str] = None  # Denormalized for filtering
    config_hash: Optional[str] = None  # Effective config at event time (CR-7 keying)
    task_name: Optional[str] = None  # Task-channel address (stage 4)
    method: Optional[str] = None  # Task-channel method (stage 4)
    worker_session_id: Optional[str] = None  # Spawn-scoped worker session (replaces ctime markers)
    actor: Optional[str] = None  # Who/what initiated (operator / agent / host id)
    worker_reported: bool = False  # Payload originated in-worker (rode the wire); host wrote the row
    payload: Dict[str, Any] = field(default_factory=dict)  # Per-event-type structured detail
    seq: Optional[int] = None  # Store-assigned cursor (rowid); None until appended


@runtime_checkable
class JournalStore(Protocol):
    """Protocol for the durable account-of-action (CR-14).

    Implementations MUST raise on append failure (loud, never silent —
    the audit trail does not degrade quietly) and MUST NOT expose a
    delete/retention surface (precious class). The synchronous tiny-INSERT
    append is the ratified design-#13 tension: buffering is the
    evidence-awaited escalation, not the default.
    """

    def append(self, event: JournalEvent) -> int:
        """Persist one event; returns the store-assigned seq (cursor)."""
        ...

    def query(
        self,
        job_id: Optional[str] = None,
        run_id: Optional[str] = None,
        composition_id: Optional[str] = None,
        capability_instance_id: Optional[str] = None,
        worker_session_id: Optional[str] = None,
        event_type: Optional[str] = None,
        after_seq: Optional[int] = None,
        since_ts: Optional[datetime] = None,
        until_ts: Optional[datetime] = None,
        limit: Optional[int] = None,
        descending: bool = False,
    ) -> List[JournalEvent]:
        """Filtered read; all filters AND-combined; `after_seq` is the tail cursor."""
        ...

    def count(self, event_type: Optional[str] = None) -> int:
        """Total rows (optionally per type) — volume regression checks."""
        ...

    def terminal_state_events(self, limit: Optional[int] = None) -> List[JournalEvent]:
        """STATE_TRANSITION rows whose payload `to` is terminal — the durable
        job history (the `_history` migration rider)."""
        ...


_JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    run_id TEXT,
    job_id TEXT,
    composition_id TEXT,
    node_id TEXT,
    capability_instance_id TEXT,
    capability_name TEXT,
    config_hash TEXT,
    task_name TEXT,
    method TEXT,
    worker_session_id TEXT,
    actor TEXT,
    worker_reported INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_journal_job ON journal (job_id);
CREATE INDEX IF NOT EXISTS idx_journal_run ON journal (run_id);
CREATE INDEX IF NOT EXISTS idx_journal_comp ON journal (composition_id);
CREATE INDEX IF NOT EXISTS idx_journal_type_ts ON journal (event_type, ts);
CREATE INDEX IF NOT EXISTS idx_journal_instance_ts ON journal (capability_instance_id, ts);
CREATE INDEX IF NOT EXISTS idx_journal_wsession ON journal (worker_session_id);
"""


class LocalJournalStore:
    """SQLite-backed default `JournalStore` (CR-14).

    WAL + busy_timeout for multi-process host writers; ONE persistent
    lock-protected connection per store instance (the stage-7 stress catch —
    see the `__init__` comment; the original per-call-connection convention
    was superseded). `append` raises on failure (loud) — callers never wrap
    it in a silent try/except.
    """

    def __init__(self, db_path: Optional[Path] = None):
        """`db_path=None` uses `~/.cjm/journal.db`; CapabilityManager passes
        `cfg.journal_db_path` (project-scoped) automatically."""
        self.db_path = Path(db_path) if db_path is not None else Path.home() / ".cjm" / "journal.db"
        # Persistent lock-protected connection (stage-7 stress part-1 catch):
        # per-call open/close to a WAL DB costs ~16 ms — the close runs a WAL
        # checkpoint — which broke the ratified "synchronous tiny WAL INSERTs"
        # latency claim 25x over. One connection per store instance +
        # synchronous=NORMAL (the standard WAL pairing) restores sub-ms
        # appends; the lock serializes intra-process threads, WAL coordinates
        # cross-process writers.
        self._lock = threading.Lock()
        self._connection: Optional[sqlite3.Connection] = None

    _SELECT_COLS = (
        "seq, event_id, ts, event_type, run_id, job_id, composition_id, "
        "node_id, capability_instance_id, capability_name, config_hash, task_name, "
        "method, worker_session_id, actor, worker_reported, payload"
    )

    @staticmethod
    def _row_to_event(row) -> JournalEvent:
        """Rehydrate a typed JournalEvent from a SELECT row."""
        (seq, event_id, ts_str, event_type, run_id, job_id, composition_id,
         node_id, capability_instance_id, capability_name, config_hash, task_name,
         method, worker_session_id, actor, worker_reported, payload_json) = row
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except (TypeError, ValueError):
            payload = {"_unparseable": payload_json}
        try:
            ts = datetime.fromisoformat(ts_str)
        except (TypeError, ValueError):
            ts = datetime.now(timezone.utc)
        return JournalEvent(
            event_type=event_type, event_id=event_id, ts=ts, run_id=run_id,
            job_id=job_id, composition_id=composition_id, node_id=node_id,
            capability_instance_id=capability_instance_id, capability_name=capability_name,
            config_hash=config_hash, task_name=task_name, method=method,
            worker_session_id=worker_session_id, actor=actor,
            worker_reported=bool(worker_reported), payload=payload, seq=seq,
        )


@patch
@contextmanager
def _conn(self: LocalJournalStore) -> Iterator[sqlite3.Connection]:
    """Yield the persistent connection under the instance lock (lazy init:
    parent dirs + WAL + schema on first use).

    Stage-7 stress catch: the previous per-call connect/close shape paid a
    WAL checkpoint on every close (~16 ms/append — 25x over the design's
    latency claim). `synchronous=NORMAL` is the standard WAL pairing
    (durable to process crash; an OS/power crash may lose only the most
    recent commits — the wedge gate covers append FAILURES, which stay
    loud). `check_same_thread=False` + the lock makes any-thread use safe.
    """
    with self._lock:
        if self._connection is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=10.0,
                                   check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_JOURNAL_SCHEMA)
            self._connection = conn
        yield self._connection


@patch
def append(
    self: LocalJournalStore,
    event: JournalEvent,  # Event to persist
) -> int:  # Store-assigned seq (cursor)
    """Persist one event; sets and returns `event.seq`.

    LOUD by contract: sqlite errors propagate (the audit trail never
    degrades silently — ratified design #13). One tiny WAL INSERT;
    synchronous on purpose (G4: the dispatch fast path must stay
    predictable; at substrate event volume this is microseconds).
    """
    with self._conn() as conn:
        cur = conn.execute(
            "INSERT INTO journal (event_id, ts, event_type, run_id, job_id, "
            "composition_id, node_id, capability_instance_id, capability_name, "
            "config_hash, task_name, method, worker_session_id, actor, "
            "worker_reported, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event.event_id, event.ts.isoformat(), event.event_type,
                event.run_id, event.job_id, event.composition_id, event.node_id,
                event.capability_instance_id, event.capability_name, event.config_hash,
                event.task_name, event.method, event.worker_session_id,
                event.actor, 1 if event.worker_reported else 0,
                json.dumps(event.payload, default=str, sort_keys=True),
            ),
        )
        conn.commit()
        event.seq = cur.lastrowid
        return event.seq


@patch
def query(
    self: LocalJournalStore,
    job_id: Optional[str] = None,  # Filter: job correlation
    run_id: Optional[str] = None,  # Filter: host-tier run
    composition_id: Optional[str] = None,  # Filter: composition
    capability_instance_id: Optional[str] = None,  # Filter: instance
    worker_session_id: Optional[str] = None,  # Filter: worker session
    event_type: Optional[str] = None,  # Filter: one vocabulary value
    after_seq: Optional[int] = None,  # Tail cursor: rows with seq > this
    since_ts: Optional[datetime] = None,  # Filter: ts >= (isoformat compare)
    until_ts: Optional[datetime] = None,  # Filter: ts <= (isoformat compare)
    limit: Optional[int] = None,  # Max rows
    descending: bool = False,  # True = newest first
) -> List[JournalEvent]:  # Matching events, seq-ordered
    """Filtered read; all filters AND-combined."""
    if not self.db_path.exists():
        return []
    clauses, params = [], []
    for col, val in (("job_id", job_id), ("run_id", run_id),
                     ("composition_id", composition_id),
                     ("capability_instance_id", capability_instance_id),
                     ("worker_session_id", worker_session_id),
                     ("event_type", event_type)):
        if val is not None:
            clauses.append(f"{col} = ?")
            params.append(val)
    if after_seq is not None:
        clauses.append("seq > ?")
        params.append(after_seq)
    if since_ts is not None:
        clauses.append("ts >= ?")
        params.append(since_ts.isoformat())
    if until_ts is not None:
        clauses.append("ts <= ?")
        params.append(until_ts.isoformat())
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    order = "DESC" if descending else "ASC"
    lim = f" LIMIT {int(limit)}" if limit is not None else ""
    sql = f"SELECT {self._SELECT_COLS} FROM journal{where} ORDER BY seq {order}{lim}"
    with self._conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [self._row_to_event(r) for r in rows]


@patch
def count(
    self: LocalJournalStore,
    event_type: Optional[str] = None,  # Optional per-type count
) -> int:  # Row count
    """Total journal rows (volume regression checks)."""
    if not self.db_path.exists():
        return 0
    with self._conn() as conn:
        if event_type is not None:
            row = conn.execute("SELECT COUNT(*) FROM journal WHERE event_type = ?",
                               (event_type,)).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM journal").fetchone()
    return int(row[0])


@patch
def terminal_state_events(
    self: LocalJournalStore,
    limit: Optional[int] = None,  # Most recent N (None = all)
) -> List[JournalEvent]:  # Terminal STATE_TRANSITION rows, newest first
    """The durable job history (`_history` migration rider): terminal
    STATE_TRANSITION rows whose payload carries the job snapshot."""
    if not self.db_path.exists():
        return []
    lim = f" LIMIT {int(limit)}" if limit is not None else ""
    sql = (f"SELECT {self._SELECT_COLS} FROM journal "
           "WHERE event_type = 'state_transition' "
           "AND json_extract(payload, '$.to') IN ('completed','failed','cancelled') "
           f"ORDER BY seq DESC{lim}")
    with self._conn() as conn:
        rows = conn.execute(sql).fetchall()
    return [self._row_to_event(r) for r in rows]
