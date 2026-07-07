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
