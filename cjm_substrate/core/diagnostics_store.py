"""CR-14 (stage 7): the disposable diagnostic-narrative class. Worker-written
structured log records (the substrate handler stamps contextvars identity —
authors never supply attribution) + the host-pumped raw stream chunks (the
zero-cooperation death-rattle floor). Retention is a QUERY, not file
mechanics. Design ledger: claude-docs/stage-7-evidence.md."""

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Protocol, runtime_checkable

from fastcore.basics import patch

_logger = logging.getLogger(__name__)


@dataclass
class DiagnosticRecord:
    """One structured worker log record (CR-14 diagnostics class)."""
    message: str  # record.getMessage() result
    level: str = "INFO"  # Logging level name
    logger_name: str = ""  # Logger hierarchy name (restored — flat logs dropped it)
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))  # tz-aware UTC
    worker_session_id: Optional[str] = None  # Spawn-scoped session
    job_id: Optional[str] = None  # EXACT correlation via contextvars (None outside a call span)
    exc_text: Optional[str] = None  # Formatted traceback when the record carried exc_info
    seq: Optional[int] = None  # Store-assigned cursor


@dataclass
class StreamChunk:
    """One raw stdout/stderr line the host pump captured (death-rattle floor).

    Attributed to the worker SESSION only, never heuristically to a job —
    stage-3 multi-lane interleaving made job-attribution of raw streams
    structurally unsound.
    """
    content: str  # Decoded line content (tqdm CR-frames collapsed to final frame)
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))  # Capture time (host clock)
    worker_session_id: Optional[str] = None  # Session attribution (the honest unit)
    stream: str = "stdout"  # Source stream (stderr merged into stdout today)
    seq: Optional[int] = None  # Store-assigned cursor


@runtime_checkable
class DiagnosticsStore(Protocol):
    """Protocol for the disposable diagnostic-narrative store (CR-14).

    Unlike the journal: MANY writers (every worker + the host pump),
    retention IS part of the contract (disposable class), and append
    failures degrade gracefully — a broken diagnostics sink must never take
    down capability execution; the loud-failure rule is the journal's alone.
    """

    def append_record(self, record: DiagnosticRecord) -> int:
        """Persist one structured record; returns seq."""
        ...

    def append_chunk(self, chunk: StreamChunk) -> int:
        """Persist one raw stream line; returns seq."""
        ...

    def query_records(
        self,
        job_id: Optional[str] = None,
        worker_session_id: Optional[str] = None,
        level: Optional[str] = None,
        after_seq: Optional[int] = None,
        limit: Optional[int] = None,
        descending: bool = False,
    ) -> List[DiagnosticRecord]:
        """Filtered structured-record read; `job_id` is EXACT (stamped, not sliced)."""
        ...

    def query_chunks(
        self,
        worker_session_id: Optional[str] = None,
        after_seq: Optional[int] = None,
        limit: Optional[int] = None,
        descending: bool = False,
    ) -> List[StreamChunk]:
        """Raw stream read, session-scoped."""
        ...

    def apply_retention(
        self,
        max_age_days: Optional[float] = None,
        max_total_mb: Optional[float] = None,
    ) -> Dict[str, int]:
        """Delete old rows by age and/or size budget; returns deleted counts."""
        ...


_DIAGNOSTICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    worker_session_id TEXT,
    job_id TEXT,
    level TEXT NOT NULL DEFAULT 'INFO',
    logger_name TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL,
    exc_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_records_job ON records (job_id);
CREATE INDEX IF NOT EXISTS idx_records_session ON records (worker_session_id);
CREATE INDEX IF NOT EXISTS idx_records_ts ON records (ts);
CREATE TABLE IF NOT EXISTS stream_chunks (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    worker_session_id TEXT,
    stream TEXT NOT NULL DEFAULT 'stdout',
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_session ON stream_chunks (worker_session_id);
CREATE INDEX IF NOT EXISTS idx_chunks_ts ON stream_chunks (ts);
"""


class LocalDiagnosticsStore:
    """SQLite-backed default `DiagnosticsStore` (CR-14).

    Many concurrent writers (workers + the host pump) -> WAL +
    busy_timeout + per-call connections (no long-held handles; safe
    from any thread). Disposable class: retention deletes are routine.
    """

    def __init__(self, db_path: Optional[Path] = None):
        """`db_path=None` uses `~/.cjm/diagnostics.db`; workers receive the
        host's path via the `CJM_DIAGNOSTICS_DB` env var at spawn."""
        self.db_path = Path(db_path) if db_path is not None else Path.home() / ".cjm" / "diagnostics.db"
        # Persistent lock-protected connection (stage-7 stress part-1 catch;
        # see LocalJournalStore._conn): per-call open/close paid a WAL
        # checkpoint per append. Worker log handlers append on the hot path.
        self._lock = threading.Lock()
        self._connection: Optional[sqlite3.Connection] = None


@patch
@contextmanager
def _conn(self: LocalDiagnosticsStore) -> Iterator[sqlite3.Connection]:
    """Yield the persistent connection under the instance lock (lazy init:
    parent dirs + WAL + schema on first use).

    Same shape + rationale as `LocalJournalStore._conn` (stage-7 stress
    catch: per-call close = WAL checkpoint = ~16 ms/append). Disposable
    class on the WORKER hot path — `synchronous=NORMAL` is plenty.
    """
    with self._lock:
        if self._connection is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=10.0,
                                   check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_DIAGNOSTICS_SCHEMA)
            self._connection = conn
        yield self._connection


@patch
def append_record(
    self: LocalDiagnosticsStore,
    record: DiagnosticRecord,  # Structured record to persist
) -> int:  # Store-assigned seq
    """Persist one structured record."""
    with self._conn() as conn:
        cur = conn.execute(
            "INSERT INTO records (ts, worker_session_id, job_id, level, "
            "logger_name, message, exc_text) VALUES (?,?,?,?,?,?,?)",
            (record.ts.isoformat(), record.worker_session_id, record.job_id,
             record.level, record.logger_name, record.message, record.exc_text),
        )
        conn.commit()
        record.seq = cur.lastrowid
        return record.seq


@patch
def append_chunk(
    self: LocalDiagnosticsStore,
    chunk: StreamChunk,  # Raw stream line to persist
) -> int:  # Store-assigned seq
    """Persist one raw stream line."""
    with self._conn() as conn:
        cur = conn.execute(
            "INSERT INTO stream_chunks (ts, worker_session_id, stream, content) "
            "VALUES (?,?,?,?)",
            (chunk.ts.isoformat(), chunk.worker_session_id, chunk.stream,
             chunk.content),
        )
        conn.commit()
        chunk.seq = cur.lastrowid
        return chunk.seq


@patch
def query_records(
    self: LocalDiagnosticsStore,
    job_id: Optional[str] = None,  # EXACT job correlation (stamped at write)
    worker_session_id: Optional[str] = None,  # Session scope
    level: Optional[str] = None,  # Level name filter
    after_seq: Optional[int] = None,  # Tail cursor
    limit: Optional[int] = None,  # Max rows
    descending: bool = False,  # True = newest first
) -> List[DiagnosticRecord]:  # Matching records, seq-ordered
    """Filtered structured-record read."""
    if not self.db_path.exists():
        return []
    clauses, params = [], []
    for col, val in (("job_id", job_id), ("worker_session_id", worker_session_id),
                     ("level", level)):
        if val is not None:
            clauses.append(f"{col} = ?")
            params.append(val)
    if after_seq is not None:
        clauses.append("seq > ?")
        params.append(after_seq)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    order = "DESC" if descending else "ASC"
    lim = f" LIMIT {int(limit)}" if limit is not None else ""
    sql = ("SELECT seq, ts, worker_session_id, job_id, level, logger_name, "
           f"message, exc_text FROM records{where} ORDER BY seq {order}{lim}")
    with self._conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for (seq, ts_str, wsid, jid, level_, name, message, exc_text) in rows:
        try:
            ts = datetime.fromisoformat(ts_str)
        except (TypeError, ValueError):
            ts = datetime.now(timezone.utc)
        out.append(DiagnosticRecord(message=message, level=level_,
                                    logger_name=name, ts=ts,
                                    worker_session_id=wsid, job_id=jid,
                                    exc_text=exc_text, seq=seq))
    return out


@patch
def query_chunks(
    self: LocalDiagnosticsStore,
    worker_session_id: Optional[str] = None,  # Session scope
    after_seq: Optional[int] = None,  # Tail cursor
    limit: Optional[int] = None,  # Max rows
    descending: bool = False,  # True = newest first
) -> List[StreamChunk]:  # Matching chunks, seq-ordered
    """Raw stream read, session-scoped."""
    if not self.db_path.exists():
        return []
    clauses, params = [], []
    if worker_session_id is not None:
        clauses.append("worker_session_id = ?")
        params.append(worker_session_id)
    if after_seq is not None:
        clauses.append("seq > ?")
        params.append(after_seq)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    order = "DESC" if descending else "ASC"
    lim = f" LIMIT {int(limit)}" if limit is not None else ""
    sql = ("SELECT seq, ts, worker_session_id, stream, content "
           f"FROM stream_chunks{where} ORDER BY seq {order}{lim}")
    with self._conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for (seq, ts_str, wsid, stream, content) in rows:
        try:
            ts = datetime.fromisoformat(ts_str)
        except (TypeError, ValueError):
            ts = datetime.now(timezone.utc)
        out.append(StreamChunk(content=content, ts=ts, worker_session_id=wsid,
                               stream=stream, seq=seq))
    return out


@patch
def apply_retention(
    self: LocalDiagnosticsStore,
    max_age_days: Optional[float] = None,  # Delete rows older than this
    max_total_mb: Optional[float] = None,  # Delete oldest rows until DB under budget
) -> Dict[str, int]:  # {'records_deleted': n, 'chunks_deleted': m}
    """Retention as a QUERY (the CR-14 reframe's mechanical payoff).

    Age first, then size: oldest rows (both tables, interleaved by ts)
    deleted in batches until the DB file is under budget. Safe against
    concurrent writers (WAL; each batch is its own transaction).
    """
    deleted = {"records_deleted": 0, "chunks_deleted": 0}
    if not self.db_path.exists():
        return deleted
    with self._conn() as conn:
        if max_age_days is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
            cur = conn.execute("DELETE FROM records WHERE ts < ?", (cutoff,))
            deleted["records_deleted"] += cur.rowcount
            cur = conn.execute("DELETE FROM stream_chunks WHERE ts < ?", (cutoff,))
            deleted["chunks_deleted"] += cur.rowcount
            conn.commit()
        if max_total_mb is not None:
            budget = max_total_mb * 1024 * 1024
            def _db_bytes():
                pc = conn.execute("PRAGMA page_count").fetchone()[0]
                ps = conn.execute("PRAGMA page_size").fetchone()[0]
                fl = conn.execute("PRAGMA freelist_count").fetchone()[0]
                return (pc - fl) * ps
            while _db_bytes() > budget:
                cur = conn.execute(
                    "DELETE FROM records WHERE seq IN "
                    "(SELECT seq FROM records ORDER BY seq ASC LIMIT 500)")
                n_rec = cur.rowcount
                cur = conn.execute(
                    "DELETE FROM stream_chunks WHERE seq IN "
                    "(SELECT seq FROM stream_chunks ORDER BY seq ASC LIMIT 500)")
                n_chk = cur.rowcount
                conn.commit()
                deleted["records_deleted"] += n_rec
                deleted["chunks_deleted"] += n_chk
                if n_rec == 0 and n_chk == 0:
                    break  # nothing left to delete; budget unreachable
        conn.commit()
    return deleted


class DiagnosticsLogHandler(logging.Handler):
    """Worker-side logging handler writing `DiagnosticRecord`s (CR-14).

    Capability authors keep calling `self.logger.*` (derive-from-behavior);
    the handler stamps call identity itself — attribution is never
    author-supplied. Thread-safe via the store's lock-protected persistent
    connection (the worker runs capability execute in an executor thread;
    contextvars propagate via copy_context at the endpoint). Never raises
    into application code.
    """

    def __init__(
        self,
        store: DiagnosticsStore,  # Sink (LocalDiagnosticsStore in-process)
        worker_session_id: Optional[str] = None,  # Spawn-scoped session id
    ):
        super().__init__()
        self.store = store
        self.worker_session_id = worker_session_id

    def emit(self, record: logging.LogRecord) -> None:
        """Write one record; job identity from the call-envelope contextvar."""
        try:
            from cjm_substrate.core.wire import get_call_envelope
            env = get_call_envelope()
            exc_text = None
            if record.exc_info:
                exc_text = logging.Formatter().formatException(record.exc_info)
            self.store.append_record(DiagnosticRecord(
                message=record.getMessage(),
                level=record.levelname,
                logger_name=record.name,
                ts=datetime.fromtimestamp(record.created, tz=timezone.utc),
                worker_session_id=self.worker_session_id,
                job_id=env.job_id if env is not None else None,
                exc_text=exc_text,
            ))
        except Exception:
            self.handleError(record)


def install_worker_diagnostics() -> Optional[DiagnosticsLogHandler]:
    """Configure worker-process logging (replaces the old `basicConfig`).

    Env contract (injected by the proxy at spawn):
    - `CJM_DIAGNOSTICS_DB`: diagnostics store path -> install the handler.
    - `CJM_WORKER_SESSION_ID`: spawn-scoped session id stamped on records.
    - `CJM_LOG_LEVEL`: operator level control (default INFO) — the old
      worker hardcoded INFO with no surface.

    Without `CJM_DIAGNOSTICS_DB` (standalone/dev import) falls back to the
    pre-CR-14 stdout `basicConfig` so nothing changes for direct runs.
    Returns the installed handler (None on fallback).
    """
    level_name = os.environ.get("CJM_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    db_path = os.environ.get("CJM_DIAGNOSTICS_DB")
    if not db_path:
        logging.basicConfig(
            level=level,
            format='%(asctime)s [%(levelname)s] %(message)s',
            force=True,
        )
        return None
    handler = DiagnosticsLogHandler(
        store=LocalDiagnosticsStore(Path(db_path)),
        worker_session_id=os.environ.get("CJM_WORKER_SESSION_ID"),
    )
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    return handler


def normalize_stream_line(
    raw: str,  # One decoded line (may contain \r progress frames)
) -> Optional[str]:  # Final frame, or None when nothing durable remains
    """Collapse CR progress frames to the final frame; drop empty results.

    tqdm renders by rewriting one terminal line with \r frames; flattened
    into a DB those frames were 52% of the whisper log's bytes. Keeping each
    line's FINAL frame preserves the durable 100% state — liveness telemetry
    is not durable (ratified design #3).
    """
    final = raw.rsplit("\r", 1)[-1].rstrip()
    return final if final else None
