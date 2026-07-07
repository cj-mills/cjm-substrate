"""Diagnostics-store tests (projected from nbs/core/diagnostics_store.ipynb
cells test-store / test-handler / test-retention / test-normalize at the
golden-reference flip; the size-budget retention loop gains coverage the
notebook never had)."""

import logging
from datetime import datetime, timedelta, timezone

from cjm_substrate.core.diagnostics_store import (DiagnosticRecord,
                                                  DiagnosticsLogHandler,
                                                  LocalDiagnosticsStore,
                                                  StreamChunk,
                                                  normalize_stream_line)
from cjm_substrate.core.wire import (CallEnvelope, reset_call_envelope,
                                     set_call_envelope)


def test_store_append_and_exact_job_correlation(tmp_path):
    store = LocalDiagnosticsStore(tmp_path / "diag.db")
    r = DiagnosticRecord(message="loading model", level="INFO",
                         logger_name="plug.Whisper", worker_session_id="ws-1",
                         job_id="job-9")
    assert store.append_record(r) == 1
    store.append_record(DiagnosticRecord(message="other job line",
                                         worker_session_id="ws-1",
                                         job_id="job-10"))
    store.append_chunk(StreamChunk(content="Detected language: English",
                                   worker_session_id="ws-1"))
    # EXACT job correlation — the timestamp-window heuristic's replacement
    got = store.query_records(job_id="job-9")
    assert len(got) == 1 and got[0].logger_name == "plug.Whisper"
    assert got[0].ts.tzinfo is not None
    chunks = store.query_chunks(worker_session_id="ws-1")
    assert len(chunks) == 1 and "Detected language" in chunks[0].content


def test_handler_stamps_contextvars_identity(tmp_path):
    # Handler stamps the call-envelope job id; never raises into app code
    store = LocalDiagnosticsStore(tmp_path / "diag.db")
    handler = DiagnosticsLogHandler(store, worker_session_id="ws-test")
    lg = logging.getLogger("test.cr14.handler")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    lg.addHandler(handler)
    try:
        token = set_call_envelope(CallEnvelope(job_id="job-ctx"))
        try:
            lg.info("inside the call span")
            try:
                raise ValueError("boom")
            except ValueError:
                lg.error("caught", exc_info=True)
        finally:
            reset_call_envelope(token)
        lg.info("outside the span")
    finally:
        lg.removeHandler(handler)
    rows = store.query_records(worker_session_id="ws-test")
    assert [r.job_id for r in rows] == ["job-ctx", "job-ctx", None]
    assert rows[1].exc_text and "ValueError: boom" in rows[1].exc_text
    assert rows[0].logger_name == "test.cr14.handler"


def test_age_based_retention_leaves_newer_rows(tmp_path):
    store = LocalDiagnosticsStore(tmp_path / "diag.db")
    old = DiagnosticRecord(message="ancient",
                           ts=datetime.now(timezone.utc) - timedelta(days=30))
    store.append_record(old)
    store.append_record(DiagnosticRecord(message="fresh"))
    store.append_chunk(StreamChunk(content="ancient chunk",
                                   ts=datetime.now(timezone.utc) - timedelta(days=30)))
    out = store.apply_retention(max_age_days=7)
    assert out == {"records_deleted": 1, "chunks_deleted": 1}
    left = store.query_records()
    assert len(left) == 1 and left[0].message == "fresh"


def test_size_budget_retention_deletes_oldest_first(tmp_path):
    store = LocalDiagnosticsStore(tmp_path / "diag.db")
    for i in range(2000):
        store.append_record(DiagnosticRecord(message=f"row {i} " + "x" * 200))
    out = store.apply_retention(max_total_mb=0.3)
    assert out["records_deleted"] > 0
    left = store.query_records()
    # Oldest rows went first — the survivors are the tail
    assert left and left[0].message.startswith("row ")
    assert int(left[0].message.split()[1]) == 2000 - len(left)


def test_retention_on_missing_db_is_noop(tmp_path):
    store = LocalDiagnosticsStore(tmp_path / "never-created.db")
    assert store.apply_retention(max_age_days=1) == {"records_deleted": 0,
                                                     "chunks_deleted": 0}
    assert store.query_records() == []
    assert store.query_chunks() == []


def test_normalize_stream_line_collapses_cr_frames():
    # tqdm CR-frame collapse: final frame kept, spam dropped
    frames = (" 24%|##4 | 2700/11257\r 49%|####9 | 5528/11257"
              "\r100%|##########| 11257/11257")
    assert normalize_stream_line(frames) == "100%|##########| 11257/11257"
    assert normalize_stream_line("plain line") == "plain line"
    assert normalize_stream_line("   \r  ") is None
