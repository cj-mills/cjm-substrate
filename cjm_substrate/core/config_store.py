"""Persistent storage for per-instance capability configuration (config + enabled flag + worker-env override), keyed by instance_id."""

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Protocol, runtime_checkable

from fastcore.basics import patch

_logger = logging.getLogger(__name__)


@dataclass
class CapabilityConfigRecord:
    """Persisted state for a capability INSTANCE: config + enabled flag + worker-env override.

    Keyed by instance_id since the workspace re-key (5daadfc4, executing the
    1f369ab2 direction): the default instance keeps `instance_id ==
    capability_name`, so legacy per-capability rows stay valid as default-instance
    records. The config/enabled pairing lives in ONE record (per CR-2's
    enable/disable design) so the substrate persists and restores both in a
    single round-trip. `worker_env` holds per-instance NON-SECRET overrides
    injected ahead of the manifest defaults at spawn (manifest-default <
    persisted-override < secret); secret values never land here."""
    config: Dict[str, Any] = field(default_factory=dict)  # Capability's current config values
    enabled: bool = True  # Whether the substrate should accept jobs for this instance
    worker_env: Dict[str, str] = field(default_factory=dict)  # Non-secret worker-env overrides for this instance
    updated_at: float = 0.0  # Unix timestamp of the last write (server clock)


@runtime_checkable
class CapabilityConfigStore(Protocol):
    """Protocol for persisting per-instance `CapabilityConfigRecord` across sessions.

    Keys are instance_ids (== capability_name for the default instance). Only
    DETERMINISTIC ids belong in the store — default and caller-derived ids
    persist; random auto-generated instances are per-run and must not be
    written. The substrate ships `LocalCapabilityConfigStore` as the default
    cross-session single-user backend; the future `cjm-workflow-state`-backed
    store (CR-2) implements the same Protocol, so hosts swap stores without
    code changes."""
    
    def get(self, instance_id: str) -> Optional[CapabilityConfigRecord]:
        """Fetch the record for an instance, or None if no record exists yet."""
        ...
    
    def set(self, instance_id: str, record: CapabilityConfigRecord) -> None:
        """Persist a record. Overwrites any prior record for the same instance.
        
        Implementations stamp `record.updated_at` to the current time during
        the write so callers don't have to manage timestamps.
        """
        ...
    
    def delete(self, instance_id: str) -> bool:
        """Remove the record for an instance. Returns True if a record was deleted."""
        ...
    
    def list_all(self) -> Dict[str, CapabilityConfigRecord]:
        """Return every stored record, keyed by instance_id."""
        ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_configs (
    instance_id TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    worker_env_json TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL
)
"""


def _default_db_path() -> Path:
    """Default SQLite location: `~/.cjm/capability_configs.db`."""
    return Path.home() / ".cjm" / "capability_configs.db"


class LocalCapabilityConfigStore:
    """SQLite-backed default implementation of `CapabilityConfigStore`.
    
    The DB is created lazily on first write. Reads against a non-existent DB
    return empty results rather than raising, so hosts can call `.get()` on
    a fresh install without preparing the file first.
    """
    
    def __init__(self, db_path: Optional[Path] = None):
        """Initialize the store. `db_path=None` uses `~/.cjm/capability_configs.db`."""
        self.db_path = Path(db_path) if db_path is not None else _default_db_path()


@patch
@contextmanager
def _conn(self:LocalCapabilityConfigStore) -> Iterator[sqlite3.Connection]:
    """Open a connection, creating parent dirs + schema on demand.

    Migrates legacy pre-workspace dbs in place (5daadfc4 re-key): renames the
    `capability_name` key column to `instance_id` (default instance_id ==
    capability_name, so legacy rows stay valid as default-instance records)
    and adds the `worker_env_json` column when absent."""
    self.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(self.db_path)
    try:
        conn.execute(_SCHEMA)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(capability_configs)")]
        if "capability_name" in cols:
            conn.execute(
                "ALTER TABLE capability_configs RENAME COLUMN capability_name TO instance_id"
            )
            cols = [c if c != "capability_name" else "instance_id" for c in cols]
        if "worker_env_json" not in cols:
            conn.execute(
                "ALTER TABLE capability_configs ADD COLUMN worker_env_json TEXT NOT NULL DEFAULT '{}'"
            )
        conn.commit()
        yield conn
    finally:
        conn.close()


@patch
def get(
    self:LocalCapabilityConfigStore,
    instance_id: str  # Instance to look up (== capability_name for the default instance)
) -> Optional[CapabilityConfigRecord]:  # Persisted record or None if absent
    """Fetch the record for an instance."""
    if not self.db_path.exists():
        return None
    with self._conn() as conn:
        row = conn.execute(
            "SELECT config_json, enabled, worker_env_json, updated_at "
            "FROM capability_configs WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
    if row is None:
        return None
    config_json, enabled, worker_env_json, updated_at = row
    try:
        config = json.loads(config_json) if config_json else {}
    except json.JSONDecodeError as e:
        _logger.warning(
            "Corrupted config row for instance %s: %s. Returning empty config.",
            instance_id, e,
        )
        config = {}
    try:
        worker_env = json.loads(worker_env_json) if worker_env_json else {}
    except json.JSONDecodeError as e:
        _logger.warning(
            "Corrupted worker_env row for instance %s: %s. Returning empty override.",
            instance_id, e,
        )
        worker_env = {}
    return CapabilityConfigRecord(
        config=config,
        enabled=bool(enabled),
        worker_env=worker_env if isinstance(worker_env, dict) else {},
        updated_at=float(updated_at),
    )


@patch
def set(
    self:LocalCapabilityConfigStore,
    instance_id: str,  # Instance to write (deterministic ids only — never auto-generated)
    record: CapabilityConfigRecord  # New record (updated_at overwritten with current time)
) -> None:
    """Persist a record. Stamps `updated_at` to the current time."""
    record.updated_at = time.time()
    with self._conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO capability_configs "
            "(instance_id, config_json, enabled, worker_env_json, updated_at) VALUES (?, ?, ?, ?, ?)",
            (
                instance_id,
                json.dumps(record.config),
                1 if record.enabled else 0,
                json.dumps(record.worker_env),
                record.updated_at,
            ),
        )
        conn.commit()


@patch
def delete(
    self:LocalCapabilityConfigStore,
    instance_id: str  # Instance to remove
) -> bool:  # True if a row was deleted
    """Remove the record for an instance."""
    if not self.db_path.exists():
        return False
    with self._conn() as conn:
        cur = conn.execute(
            "DELETE FROM capability_configs WHERE instance_id = ?", (instance_id,),
        )
        conn.commit()
        return cur.rowcount > 0


@patch
def list_all(self:LocalCapabilityConfigStore) -> Dict[str, CapabilityConfigRecord]:  # instance_id -> record
    """Return all stored records keyed by instance_id."""
    if not self.db_path.exists():
        return {}
    with self._conn() as conn:
        rows = conn.execute(
            "SELECT instance_id, config_json, enabled, worker_env_json, updated_at "
            "FROM capability_configs",
        ).fetchall()
    out: Dict[str, CapabilityConfigRecord] = {}
    for instance_id, config_json, enabled, worker_env_json, updated_at in rows:
        try:
            config = json.loads(config_json) if config_json else {}
        except json.JSONDecodeError:
            config = {}
        try:
            worker_env = json.loads(worker_env_json) if worker_env_json else {}
        except json.JSONDecodeError:
            worker_env = {}
        out[instance_id] = CapabilityConfigRecord(
            config=config,
            enabled=bool(enabled),
            worker_env=worker_env if isinstance(worker_env, dict) else {},
            updated_at=float(updated_at),
        )
    return out
