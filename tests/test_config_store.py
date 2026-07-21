"""Config-store tests (projected from nbs/core/config_store.ipynb cell smoke-test
at the golden-reference flip): SG-22 Protocol satisfaction + record round-trips."""

from cjm_substrate.core.config_store import (CapabilityConfigRecord, CapabilityConfigStore,
                                             LocalCapabilityConfigStore)


def test_protocol_satisfaction_and_empty_store_safety(tmp_path):
    store = LocalCapabilityConfigStore(tmp_path / "configs.db")
    # runtime_checkable Protocol enables the isinstance seam
    assert isinstance(store, CapabilityConfigStore)
    # Empty store: missing reads return None / {} / False — never raise
    assert store.get("whisper") is None
    assert store.list_all() == {}
    assert store.delete("whisper") is False


def test_record_round_trip_overwrite_and_delete(tmp_path):
    store = LocalCapabilityConfigStore(tmp_path / "configs.db")

    store.set("whisper", CapabilityConfigRecord(config={"model": "large-v3"}, enabled=False))
    out = store.get("whisper")
    assert out is not None
    assert out.config == {"model": "large-v3"}
    assert out.enabled is False
    assert out.updated_at > 0

    # Overwrite + list_all
    store.set("whisper", CapabilityConfigRecord(config={"model": "tiny"}, enabled=True))
    store.set("gemini", CapabilityConfigRecord(config={"api_key": "x"}, enabled=True))
    all_records = store.list_all()
    assert set(all_records.keys()) == {"whisper", "gemini"}
    assert all_records["whisper"].config == {"model": "tiny"}
    assert all_records["whisper"].enabled is True

    # Delete returns True on hit, False on the second call
    assert store.delete("whisper") is True
    assert store.delete("whisper") is False
    assert store.get("whisper") is None
    assert set(store.list_all().keys()) == {"gemini"}


def test_worker_env_round_trip_and_instance_keying(tmp_path):
    store = LocalCapabilityConfigStore(tmp_path / "configs.db")
    # 5daadfc4 re-key: records key by instance_id — two instances of one
    # capability hold independent state (the 1f369ab2 voxtral scenario)
    store.set("voxtral", CapabilityConfigRecord(config={"device": "cuda"}))
    store.set("voxtral--mini", CapabilityConfigRecord(
        config={"device": "cpu"}, worker_env={"OMP_NUM_THREADS": "8"},
    ))
    assert store.get("voxtral").config == {"device": "cuda"}
    assert store.get("voxtral").worker_env == {}
    mini = store.get("voxtral--mini")
    assert mini.config == {"device": "cpu"}
    assert mini.worker_env == {"OMP_NUM_THREADS": "8"}
    assert set(store.list_all()) == {"voxtral", "voxtral--mini"}


def test_legacy_capability_name_schema_migrates(tmp_path):
    import json as _json
    import sqlite3
    import time as _time
    db = tmp_path / "configs.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE capability_configs ("
        "capability_name TEXT PRIMARY KEY, config_json TEXT NOT NULL, "
        "enabled INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO capability_configs VALUES (?, ?, ?, ?)",
        ("whisper", _json.dumps({"model": "large-v3"}), 0, _time.time()),
    )
    conn.commit()
    conn.close()

    store = LocalCapabilityConfigStore(db)
    # Legacy per-capability row survives as the default instance's record
    # (default instance_id == capability_name)
    rec = store.get("whisper")
    assert rec is not None
    assert rec.config == {"model": "large-v3"}
    assert rec.enabled is False
    assert rec.worker_env == {}
    # The migrated schema accepts new-shape writes
    store.set("whisper--tiny", CapabilityConfigRecord(worker_env={"HF_HOME": "/x"}))
    assert store.get("whisper--tiny").worker_env == {"HF_HOME": "/x"}
