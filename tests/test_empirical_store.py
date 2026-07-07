"""Empirical-store tests (projected from nbs/core/empirical_store.ipynb cells
test-compute-config-hash / test-welford-aggregation / test-multi-instance-keying /
test-delete-empty-db / b5ba9ddf at the golden-reference flip)."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cjm_substrate.core.empirical_store import (compute_config_hash, EmpiricalResourceStore,
                                                LocalEmpiricalResourceStore, ResourceSample)


def _sample(cpu=0.0, mem=0.0, gpu=0.0, dur=0.1, success=True, usage=None):
    return ResourceSample(cpu_percent=cpu, memory_mb_peak=mem, gpu_memory_mb_peak=gpu,
                          duration_seconds=dur, success=success,
                          observed_at=datetime.now(timezone.utc), api_usage=usage)


def test_compute_config_hash_canonicalization():
    # insertion-order independent, None == {}, sha256-tagged, value-sensitive
    h1 = compute_config_hash({"model": "base", "device": "cuda"})
    h2 = compute_config_hash({"device": "cuda", "model": "base"})
    assert h1 == h2, "insertion-order must not affect the hash"
    assert compute_config_hash(None) == compute_config_hash({})
    assert h1.startswith("sha256:")
    assert h1 != compute_config_hash({"model": "large"})


def test_welford_mean_max_of_peaks_and_success_rate(tmp_path):
    store = LocalEmpiricalResourceStore(tmp_path / "e.db")
    assert store.get_record("whisper", "abc") is None
    assert store.list_records() == []

    samples = [
        _sample(cpu=20.0, mem=1000.0, gpu=5000.0, dur=10.0, success=True),
        _sample(cpu=40.0, mem=1500.0, gpu=7000.0, dur=20.0, success=True),
        _sample(cpu=60.0, mem=1200.0, gpu=6500.0, dur=15.0, success=False),
    ]
    for s in samples:
        store.record_sample("whisper-base", "whisper", "sha256:abc", s)

    rec = store.get_record("whisper-base", "sha256:abc")
    assert rec is not None and rec.sample_count == 3
    # Welford must match the independently-computed arithmetic mean
    assert abs(rec.cpu_percent_mean - (20.0 + 40.0 + 60.0) / 3) < 1e-9
    assert abs(rec.memory_mb_peak_mean - (1000.0 + 1500.0 + 1200.0) / 3) < 1e-9
    assert abs(rec.gpu_memory_mb_peak_mean - (5000.0 + 7000.0 + 6500.0) / 3) < 1e-9
    assert abs(rec.duration_seconds_mean - (10.0 + 20.0 + 15.0) / 3) < 1e-9
    # Max-of-peaks tracks the worst observation
    assert rec.memory_mb_peak_max == 1500.0
    assert rec.gpu_memory_mb_peak_max == 7000.0
    assert abs(rec.success_rate - 2 / 3) < 1e-9
    assert rec.last_observed.tzinfo is not None, "last_observed must be tz-aware"


def test_multi_instance_and_multi_config_keying(tmp_path):
    store = LocalEmpiricalResourceStore(tmp_path / "e.db")
    s1 = _sample(cpu=10.0, mem=100.0, dur=1.0)
    s2 = _sample(cpu=50.0, mem=500.0, dur=5.0)

    # Same instance, two configs — separate records
    store.record_sample("whisper", "whisper", compute_config_hash({"model": "base"}), s1)
    store.record_sample("whisper", "whisper", compute_config_hash({"model": "large"}), s2)
    assert store.get_record("whisper", compute_config_hash({"model": "base"})).cpu_percent_mean == 10.0
    assert store.get_record("whisper", compute_config_hash({"model": "large"})).cpu_percent_mean == 50.0

    # Same config, two instances (CR-10 multi-instance) — separate records
    cfg_hash = compute_config_hash({"model": "base"})
    store.record_sample("whisper-a", "whisper", cfg_hash, s1)
    store.record_sample("whisper-b", "whisper", cfg_hash, s2)
    assert store.get_record("whisper-a", cfg_hash).cpu_percent_mean == 10.0
    assert store.get_record("whisper-b", cfg_hash).cpu_percent_mean == 50.0

    assert len(store.list_records()) == 4
    assert len(store.list_records(capability_name="whisper")) == 4
    assert store.list_records(capability_name="nonexistent") == []


def test_delete_record_and_empty_db_safety(tmp_path):
    # Non-existent DB: reads return None/empty, delete misses — never raises
    store = LocalEmpiricalResourceStore(tmp_path / "e.db")
    assert store.get_record("x", "y") is None
    assert store.list_records() == []
    assert store.delete_record("x", "y") is False

    store.record_sample("a", "a", "sha256:xyz", _sample(cpu=1.0, mem=1.0, dur=1.0))
    assert store.get_record("a", "sha256:xyz") is not None
    assert store.delete_record("a", "sha256:xyz") is True
    assert store.get_record("a", "sha256:xyz") is None
    assert store.delete_record("a", "sha256:xyz") is False, "second delete is a miss"

    # Protocol type-check works (runtime_checkable)
    assert isinstance(store, EmpiricalResourceStore)


def test_sg54_api_usage_totals_accumulate(tmp_path):
    # SG-54: usage is ADDITIVE (cumulative), unit-agnostic; absent usage leaves {}
    store = LocalEmpiricalResourceStore(tmp_path / "e.db")
    store.record_sample("gem", "gemini", "h1",
                        _sample(usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}))
    store.record_sample("gem", "gemini", "h1",
                        _sample(usage={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}))
    rec = store.get_record("gem", "h1")
    assert rec.api_usage_totals == {"input_tokens": 12.0, "output_tokens": 8.0, "total_tokens": 20.0}
    assert rec.sample_count == 2
    # A run reporting a NEW unit key accumulates independently
    store.record_sample("gem", "gemini", "h1", _sample(usage={"requests": 1, "input_tokens": 1}))
    rec = store.get_record("gem", "h1")
    assert rec.api_usage_totals["requests"] == 1.0
    assert rec.api_usage_totals["input_tokens"] == 13.0
    # Compute-only capability (no usage reported) -> empty totals
    store.record_sample("cpu", "nltk", "h2", _sample(usage=None))
    assert store.get_record("cpu", "h2").api_usage_totals == {}


def test_sg54_pre_migration_db_upgrades_in_place(tmp_path):
    # A pre-SG-54 DB (no api_usage_totals column) ALTERs in on first open
    dbp = tmp_path / "old.db"
    conn = sqlite3.connect(dbp)
    conn.execute(
        "CREATE TABLE empirical_resources ("
        "instance_id TEXT NOT NULL, capability_name TEXT NOT NULL, config_hash TEXT NOT NULL, "
        "sample_count INTEGER NOT NULL DEFAULT 0, success_count INTEGER NOT NULL DEFAULT 0, "
        "cpu_percent_mean REAL NOT NULL DEFAULT 0.0, memory_mb_peak_max REAL NOT NULL DEFAULT 0.0, "
        "memory_mb_peak_mean REAL NOT NULL DEFAULT 0.0, gpu_memory_mb_peak_max REAL NOT NULL DEFAULT 0.0, "
        "gpu_memory_mb_peak_mean REAL NOT NULL DEFAULT 0.0, duration_seconds_mean REAL NOT NULL DEFAULT 0.0, "
        "last_observed TEXT NOT NULL, PRIMARY KEY (instance_id, config_hash))"
    )
    conn.execute(
        "INSERT INTO empirical_resources VALUES ('old','p','h',1,1,0,0,0,0,0,0.1,?)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()
    conn.close()

    store = LocalEmpiricalResourceStore(dbp)
    rec = store.get_record("old", "h")
    assert rec is not None and rec.api_usage_totals == {}  # old row reads as {}
    store.record_sample("old", "p", "h", _sample(usage={"total_tokens": 7}))
    assert store.get_record("old", "h").api_usage_totals == {"total_tokens": 7.0}
