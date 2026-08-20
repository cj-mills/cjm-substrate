"""Tests for the SidecarState store (never-raise load, merge-on-save,
best-effort write)."""
from cjm_substrate.utils.sidecar import SidecarState


def test_round_trip_and_merge(tmp_path):
    store = SidecarState(tmp_path / "state.json")
    assert store.load() == {}                   # absent -> {}
    store.save(a=1)
    store.save(b=2)
    assert store.load() == {"a": 1, "b": 2}     # merge, not clobber
    store.save(a=3)
    assert store.load() == {"a": 3, "b": 2}


def test_corrupt_and_unwritable_tolerated(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    store = SidecarState(p)
    assert store.load() == {}                   # corrupt -> {}
    store.save(k=1)                             # recovers by rewriting
    assert store.load()["k"] == 1
    missing = SidecarState(tmp_path / "no-dir" / "state.json")
    missing.save(k=1)                           # parent absent: silently tolerated
    assert missing.load() == {}
