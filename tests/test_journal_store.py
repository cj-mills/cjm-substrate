"""Journal-store tests (projected from nbs/core/journal_store.ipynb cells
test-roundtrip / test-terminal / test-tolerant-unknown / test-loud at the
golden-reference flip)."""

import pytest

from cjm_substrate.core.journal_store import (LIVENESS_EVENT_TYPES,
                                              JournalEvent, LocalJournalStore,
                                              SubstrateEventType)


def test_append_query_roundtrip_and_cursor(tmp_path):
    store = LocalJournalStore(tmp_path / "journal.db")
    ev = JournalEvent(event_type="state_transition", job_id="job-1",
                      capability_instance_id="plug-a",
                      payload={"from": "pending", "to": "running"})
    seq1 = store.append(ev)
    assert seq1 == 1 and ev.seq == 1
    ev2 = JournalEvent(event_type=SubstrateEventType.WORKER_SPAWNED.value,
                       capability_name="plug-a", worker_session_id="ws-1",
                       payload={"pid": 1234})
    assert store.append(ev2) == 2
    # round-trip: typed rehydration with tz-aware ts + payload intact
    got = store.query(job_id="job-1")
    assert len(got) == 1 and got[0].event_id == ev.event_id
    assert got[0].ts.tzinfo is not None
    assert got[0].payload == {"from": "pending", "to": "running"}
    assert got[0].seq == 1 and got[0].worker_reported is False
    # cursor semantics: after_seq is the live-tail catch-up
    tail = store.query(after_seq=1)
    assert [e.seq for e in tail] == [2]
    assert store.count() == 2
    assert store.count(event_type="worker_spawned") == 1


def test_terminal_state_events_history_query(tmp_path):
    store = LocalJournalStore(tmp_path / "journal.db")
    for jid, to in (("j1", "running"), ("j1", "completed"), ("j2", "running"),
                    ("j2", "failed"), ("j3", "running")):
        store.append(JournalEvent(event_type="state_transition", job_id=jid,
                                  payload={"from": "x", "to": to,
                                           "job_snapshot": {"id": jid}}))
    term = store.terminal_state_events()
    # newest first; only terminal transitions; snapshots intact
    assert [e.job_id for e in term] == ["j2", "j1"]
    assert term[0].payload["job_snapshot"] == {"id": "j2"}
    assert [e.job_id for e in store.terminal_state_events(limit=1)] == ["j2"]


def test_unknown_event_types_roundtrip_untouched(tmp_path):
    # Vocabulary tolerance: the P5/P6 tolerant-unknown law
    store = LocalJournalStore(tmp_path / "journal.db")
    store.append(JournalEvent(event_type="some_future_event_kind",
                              payload={"x": 1}))
    got = store.query(event_type="some_future_event_kind")
    assert len(got) == 1 and got[0].payload == {"x": 1}


def test_liveness_routing_constants():
    # Values must match the JobEventType strings they exclude (this module
    # never imports the queue — dependency direction: queue -> journal_store)
    assert LIVENESS_EVENT_TYPES == {"progress_changed", "resource_snapshot"}


def test_append_is_loud_on_storage_failure():
    # Loud-failure contract (ratified design #13): append on an unwritable
    # path RAISES, never silently drops the audit trail
    bad = LocalJournalStore(__import__("pathlib").Path(
        "/proc/nonexistent-dir/journal.db"))
    with pytest.raises(Exception):
        bad.append(JournalEvent(event_type="x"))


def test_query_missing_db_and_count_zero(tmp_path):
    store = LocalJournalStore(tmp_path / "never-created.db")
    assert store.query() == []
    assert store.count() == 0
    assert store.terminal_state_events() == []
