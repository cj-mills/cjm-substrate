"""Persistent storage for per-capability configuration (with enabled flag)."""

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
    """Persisted state for a capability: config dict + enabled flag.

    The pairing lives in ONE record (per CR-2's enable/disable design) so the
    substrate persists and restores both in a single round-trip."""
    config: Dict[str, Any] = field(default_factory=dict)  # Capability's current config values
    enabled: bool = True  # Whether the substrate should accept jobs for this capability
    updated_at: float = 0.0  # Unix timestamp of the last write (server clock)


@runtime_checkable
class CapabilityConfigStore(Protocol):
    """Protocol for persisting per-capability `CapabilityConfigRecord` across sessions.

    The substrate ships `LocalCapabilityConfigStore` as the default cross-session
    single-user backend; the future `cjm-workflow-state`-backed store (CR-2)
    implements the same Protocol, so hosts swap stores without code changes."""
    
    def get(self, capability_name: str) -> Optional[CapabilityConfigRecord]:
        """Fetch the record for a capability, or None if no record exists yet."""
        ...
    
    def set(self, capability_name: str, record: CapabilityConfigRecord) -> None:
        """Persist a record. Overwrites any prior record for the same capability.
        
        Implementations stamp `record.updated_at` to the current time during
        the write so callers don't have to manage timestamps.
        """
        ...
    
    def delete(self, capability_name: str) -> bool:
        """Remove the record for a capability. Returns True if a record was deleted."""
        ...
    
    def list_all(self) -> Dict[str, CapabilityConfigRecord]:
        """Return every stored record, keyed by capability name."""
        ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_configs (
    capability_name TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
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
    """Open a connection, creating parent dirs + schema on demand."""
    self.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(self.db_path)
    try:
        conn.execute(_SCHEMA)
        conn.commit()
        yield conn
    finally:
        conn.close()


@patch
def get(
    self:LocalCapabilityConfigStore,
    capability_name: str  # Capability to look up
) -> Optional[CapabilityConfigRecord]:  # Persisted record or None if absent
    """Fetch the record for a capability."""
    if not self.db_path.exists():
        return None
    with self._conn() as conn:
        row = conn.execute(
            "SELECT config_json, enabled, updated_at FROM capability_configs WHERE capability_name = ?",
            (capability_name,),
        ).fetchone()
    if row is None:
        return None
    config_json, enabled, updated_at = row
    try:
        config = json.loads(config_json) if config_json else {}
    except json.JSONDecodeError as e:
        _logger.warning(
            "Corrupted config row for capability %s: %s. Returning empty config.",
            capability_name, e,
        )
        config = {}
    return CapabilityConfigRecord(
        config=config,
        enabled=bool(enabled),
        updated_at=float(updated_at),
    )


@patch
def set(
    self:LocalCapabilityConfigStore,
    capability_name: str,  # Capability to write
    record: CapabilityConfigRecord  # New record (updated_at overwritten with current time)
) -> None:
    """Persist a record. Stamps `updated_at` to the current time."""
    record.updated_at = time.time()
    with self._conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO capability_configs "
            "(capability_name, config_json, enabled, updated_at) VALUES (?, ?, ?, ?)",
            (
                capability_name,
                json.dumps(record.config),
                1 if record.enabled else 0,
                record.updated_at,
            ),
        )
        conn.commit()


@patch
def delete(
    self:LocalCapabilityConfigStore,
    capability_name: str  # Capability to remove
) -> bool:  # True if a row was deleted
    """Remove the record for a capability."""
    if not self.db_path.exists():
        return False
    with self._conn() as conn:
        cur = conn.execute(
            "DELETE FROM capability_configs WHERE capability_name = ?", (capability_name,),
        )
        conn.commit()
        return cur.rowcount > 0


@patch
def list_all(self:LocalCapabilityConfigStore) -> Dict[str, CapabilityConfigRecord]:  # capability_name -> record
    """Return all stored records keyed by capability name."""
    if not self.db_path.exists():
        return {}
    with self._conn() as conn:
        rows = conn.execute(
            "SELECT capability_name, config_json, enabled, updated_at FROM capability_configs",
        ).fetchall()
    out: Dict[str, CapabilityConfigRecord] = {}
    for name, config_json, enabled, updated_at in rows:
        try:
            config = json.loads(config_json) if config_json else {}
        except json.JSONDecodeError:
            config = {}
        out[name] = CapabilityConfigRecord(
            config=config,
            enabled=bool(enabled),
            updated_at=float(updated_at),
        )
    return out
