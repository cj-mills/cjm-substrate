"""Universal Worker tests (projected from nbs/core/worker.ipynb #|hide cells at
the golden-reference flip): CR-3 monitor status-code taxonomy (404/501),
CR-4 endpoints (/prefetch, /reconfigure, 409-vs-500), SG-51/SG-52
/execute_stream contract (cancel-flag reset + typed _job_error terminal
chunks), CR-14 call-envelope propagation into the executor thread, the CR-14
follow-up X-CJM-Accounts header, and the EnhancedJSONEncoder round-trip
(datetime case added — the notebook only covered dataclasses).

Stub capabilities are injected as a fake module in sys.modules so create_app's
dynamic-import path finds them (the notebook's trick, kept)."""

import json
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from cjm_substrate.core.errors import CapabilityCancelledError
from cjm_substrate.core.wire import (
    ACCOUNTS_HEADER, get_call_envelope, record_account,
)
from cjm_substrate.core.worker import EnhancedJSONEncoder, create_app

STUB_MODULE = "_worker_test_stubs"


# ---------------------------------------------------------------------------
# Stub capabilities (one fake module; create_app dynamic-imports from it)
# ---------------------------------------------------------------------------

class NotAMonitorCapability:
    """NOT a monitor — lacks get_system_status / list_processes entirely.
    Worker should return 404 (configuration error: wrong capability type)."""
    name = "not-a-monitor"
    version = "1.0.0"
    def initialize(self, config=None): pass
    def execute(self, *args, **kwargs): return {"ok": True}
    def get_config_schema(self): return {}
    def get_current_config(self): return {}


class RaisingMonitorCapability:
    """IS a monitor shape but raises NotImplementedError — a legacy monitor
    opting out of the CR-3 typed surface. Worker should return 501 (the proxy
    then falls back to /execute)."""
    name = "raising-monitor"
    version = "1.0.0"
    def initialize(self, config=None): pass
    def execute(self, command="get_system_status", **kwargs):
        if command == "get_system_status":
            return {"gpu_type": "LEGACY", "cpu_percent": 12.0}
        return {}
    def get_config_schema(self): return {}
    def get_current_config(self): return {}
    def get_system_status(self):
        raise NotImplementedError("opted out of typed surface")
    def list_processes(self):
        raise NotImplementedError("opted out of typed surface")


class PrefetchCapability:
    """Tracks prefetch + reconfigure invocations for endpoint verification."""
    name = "cr4-prefetch-capability"
    version = "1.0.0"
    # Class-level so tests can verify across instances; the worker creates one
    # instance via capability_cls() so a class-level counter is fine.
    prefetch_calls = 0
    reconfigure_calls = []  # list of (old, new) tuples
    def initialize(self, config=None): pass
    def execute(self, *args, **kwargs): return {"ok": True}
    def get_config_schema(self): return {}
    def get_current_config(self): return {}
    def prefetch(self):
        type(self).prefetch_calls += 1
    def reconfigure(self, old_config, new_config):
        type(self).reconfigure_calls.append((old_config, new_config))


class PrefetchRaisingCapability(PrefetchCapability):
    """prefetch() raises — worker returns 500 with detail."""
    name = "cr4-prefetch-raising-capability"
    def prefetch(self):
        raise RuntimeError("prefetch boom")


class CancellingCapability:
    """execute() raises CapabilityCancelledError to drive the 409 path."""
    name = "cr4-cancelling-capability"
    version = "1.0.0"
    _cancel_requested = False  # CR-4 flag (class-level default)
    def initialize(self, config=None): pass
    def execute(self, *args, **kwargs):
        # In real use capability code calls self.check_cancel() and it raises
        # when the substrate has flipped the flag via /cancel; this stub raises
        # directly to keep the test independent of timing.
        raise CapabilityCancelledError(self.name)
    def get_config_schema(self): return {}
    def get_current_config(self): return {}


class FailingCapability:
    """execute() raises a real RuntimeError — worker returns 500."""
    name = "cr4-failing-capability"
    version = "1.0.0"
    def initialize(self, config=None): pass
    def execute(self, *args, **kwargs):
        raise RuntimeError("simulated capability failure")
    def get_config_schema(self): return {}
    def get_current_config(self): return {}


class StreamCapability:
    """execute_stream yields items normally; verifies SG-51's flag reset."""
    name = "sg51-stream-capability"
    version = "1.0.0"
    _cancel_requested = True  # Pre-set: the SG-51 reset must clear it
    def initialize(self, config=None): pass
    def execute(self, *args, **kwargs): return None
    def get_config_schema(self): return {}
    def get_current_config(self): return {}
    def execute_stream(self, *args, **kwargs):
        # The capability captures the flag's value at yield-time so the test
        # can verify the worker reset it before iteration started.
        yield {"chunk": 0, "cancel_flag_at_start": self._cancel_requested}
        yield {"chunk": 1}
        yield {"chunk": 2}


class CancellingStreamCapability:
    """execute_stream raises CapabilityCancelledError mid-stream — SG-52 typed
    error chunk emission + category mapping for cancellation."""
    name = "sg52-cancel-capability"
    version = "1.0.0"
    _cancel_requested = False
    def initialize(self, config=None): pass
    def execute(self, *args, **kwargs): return None
    def get_config_schema(self): return {}
    def get_current_config(self): return {}
    def execute_stream(self, *args, **kwargs):
        yield {"data": "first"}
        raise CapabilityCancelledError(self.name)


class FailingStreamCapability:
    """execute_stream raises a bare RuntimeError — SG-52 typed error chunk with
    CR-5 default classification (RuntimeError -> fatal, retriable=False)."""
    name = "sg52-fail-capability"
    version = "1.0.0"
    def initialize(self, config=None): pass
    def execute(self, *args, **kwargs): return None
    def get_config_schema(self): return {}
    def get_current_config(self): return {}
    def execute_stream(self, *args, **kwargs):
        yield {"data": "before failure"}
        raise RuntimeError("simulated stream failure")


class EnvelopeCapability:
    """Captures the contextvar AS SEEN INSIDE execute() (executor thread)."""
    name = "cr14-envelope-capability"
    version = "1.0.0"
    seen_envelopes = []  # class-level capture across calls
    def initialize(self, config=None): pass
    def execute(self, *args, **kwargs):
        type(self).seen_envelopes.append(get_call_envelope())
        return {"ok": True}
    def get_config_schema(self): return {}
    def get_current_config(self): return {}


class AcctRecordingCapability:
    """execute records accounts (the T29 storage-helper shape)."""
    name = "acct-recording-capability"
    version = "1.0.0"
    def initialize(self, config=None): pass
    def execute(self, *args, mode="save", **kwargs):
        if mode == "save":
            record_account("result_saved", {"row_job_id": "row-1",
                                            "text_hash": "sha256:abc"})
            return {"ok": True}
        if mode == "save_then_fail":
            record_account("result_saved", {"row_job_id": "row-2"})
            raise RuntimeError("post-save crash")
        return {"ok": True}  # mode="plain": no accounts recorded
    def get_config_schema(self): return {}
    def get_current_config(self): return {}


class AcctTaskAdapter:
    """Minimal task adapter: /task dispatch target for the TASK_ACCOUNT test."""
    task_name = "acct-task"
    def __init__(self, tool): self.tool = tool
    def do_thing(self, x=0):
        if x < 0:
            raise RuntimeError("negative")
        return {"x": x}


_stub_mod = types.ModuleType(STUB_MODULE)
for _cls in (NotAMonitorCapability, RaisingMonitorCapability, PrefetchCapability,
             PrefetchRaisingCapability, CancellingCapability, FailingCapability,
             StreamCapability, CancellingStreamCapability, FailingStreamCapability,
             EnvelopeCapability, AcctRecordingCapability, AcctTaskAdapter):
    setattr(_stub_mod, _cls.__name__, _cls)
sys.modules[STUB_MODULE] = _stub_mod


def _read_ndjson_chunks(response):
    return [json.loads(line) for line in response.iter_lines() if line]


# ---------------------------------------------------------------------------
# EnhancedJSONEncoder
# ---------------------------------------------------------------------------

def test_enhanced_json_encoder_dataclass():
    @dataclass
    class SampleConfig:
        name: str
        value: int

    result = json.dumps(SampleConfig(name="test", value=42),
                        cls=EnhancedJSONEncoder)
    assert json.loads(result) == {"name": "test", "value": 42}


def test_enhanced_json_encoder_datetime():
    # SG-52: datetime support so JobError.occurred_at serializes cleanly.
    ts = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)
    assert json.loads(json.dumps({"at": ts}, cls=EnhancedJSONEncoder)) == \
        {"at": ts.isoformat()}


# ---------------------------------------------------------------------------
# CR-3: monitor endpoint status-code taxonomy
# ---------------------------------------------------------------------------

def test_monitor_endpoints_404_when_not_a_monitor():
    # Loud configuration error: substrate's system_monitor wired wrong.
    app = create_app(STUB_MODULE, "NotAMonitorCapability")
    with TestClient(app) as client:
        resp = client.post("/get_system_status")
        assert resp.status_code == 404, f"expected 404, got {resp.status_code}: {resp.text}"
        assert "not a monitor capability" in resp.json()["detail"]

        resp = client.post("/list_processes")
        assert resp.status_code == 404, f"expected 404, got {resp.status_code}: {resp.text}"
        assert "not a monitor capability" in resp.json()["detail"]


def test_monitor_endpoints_501_when_opted_out():
    # Monitor shape but NotImplementedError: proxy falls back to /execute
    # returning the legacy dict shape; worker's job is to surface 501 cleanly.
    app = create_app(STUB_MODULE, "RaisingMonitorCapability")
    with TestClient(app) as client:
        resp = client.post("/get_system_status")
        assert resp.status_code == 501, f"expected 501, got {resp.status_code}: {resp.text}"
        assert "opted out" in resp.json()["detail"]

        resp = client.post("/list_processes")
        assert resp.status_code == 501, f"expected 501, got {resp.status_code}: {resp.text}"

        # Sanity: /execute still works on the same capability (the proxy's
        # 501 fallback path depends on this).
        resp = client.post("/execute",
                           json={"args": [], "kwargs": {"command": "get_system_status"}})
        assert resp.status_code == 200, f"/execute should work: {resp.status_code} {resp.text}"
        assert resp.json() == {"gpu_type": "LEGACY", "cpu_percent": 12.0}


# ---------------------------------------------------------------------------
# CR-4: /prefetch, /reconfigure, 409-vs-500 on /execute
# ---------------------------------------------------------------------------

def test_prefetch_and_reconfigure():
    PrefetchCapability.prefetch_calls = 0
    PrefetchCapability.reconfigure_calls = []
    app = create_app(STUB_MODULE, "PrefetchCapability")
    with TestClient(app) as client:
        resp = client.post("/prefetch")
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
        assert resp.json()["status"] == "prefetched"
        assert PrefetchCapability.prefetch_calls == 1, "prefetch hook must fire"

        resp = client.post("/reconfigure", json={
            "old_config": {"model": "base"},
            "new_config": {"model": "large"},
        })
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
        assert resp.json()["status"] == "reconfigured"
        assert PrefetchCapability.reconfigure_calls == [
            ({"model": "base"}, {"model": "large"})
        ], f"reconfigure args wrong: {PrefetchCapability.reconfigure_calls}"


def test_prefetch_raising_returns_500():
    # NOT 200 + status=error; mirrors /execute's "raised -> 500" semantics.
    app = create_app(STUB_MODULE, "PrefetchRaisingCapability")
    with TestClient(app) as client:
        resp = client.post("/prefetch")
        assert resp.status_code == 500, f"expected 500 on raising prefetch, got {resp.status_code}"
        assert "prefetch boom" in resp.json()["detail"]


def test_execute_cancellation_surfaces_as_409():
    app = create_app(STUB_MODULE, "CancellingCapability")
    with TestClient(app) as client:
        resp = client.post("/execute", json={"args": [], "kwargs": {}})
        assert resp.status_code == 409, \
            f"CapabilityCancelledError must surface as 409 not {resp.status_code}: {resp.text}"
        assert "cr4-cancelling-capability" in resp.json()["detail"]


def test_execute_real_failure_stays_500_with_job_error():
    # Proves the 409 path is specifically for CapabilityCancelledError, and
    # (G7) the 500 body carries the typed _job_error sentinel — SG-52 parity
    # for the unary path — instead of a bare-string detail.
    app = create_app(STUB_MODULE, "FailingCapability")
    with TestClient(app) as client:
        resp = client.post("/execute", json={"args": [], "kwargs": {}})
        assert resp.status_code == 500, f"real failure must remain 500, got {resp.status_code}"
        body = resp.json()
        assert "_job_error" in body, f"expected _job_error sentinel: {body}"
        assert "simulated capability failure" in body["_job_error"]["message"]
        assert body["_job_error"].get("category"), "JobError category must survive the wire"


# ---------------------------------------------------------------------------
# SG-51 + SG-52: /execute_stream contract
# ---------------------------------------------------------------------------

def test_stream_resets_cancel_flag_before_iteration():
    app = create_app(STUB_MODULE, "StreamCapability")
    with TestClient(app) as client:
        with client.stream("POST", "/execute_stream",
                           json={"args": [], "kwargs": {}}) as resp:
            assert resp.status_code == 200
            chunks = _read_ndjson_chunks(resp)
        # SG-51 invariant: the leftover _cancel_requested=True must be cleared
        # by the worker before the first chunk is yielded.
        assert chunks[0]["cancel_flag_at_start"] is False, \
            f"SG-51: worker did NOT reset _cancel_requested; got {chunks[0]}"
        assert chunks == [
            {"chunk": 0, "cancel_flag_at_start": False},
            {"chunk": 1},
            {"chunk": 2},
        ], f"unexpected chunks: {chunks}"


def test_stream_cancellation_emits_typed_terminal_chunk():
    app = create_app(STUB_MODULE, "CancellingStreamCapability")
    with TestClient(app) as client:
        with client.stream("POST", "/execute_stream",
                           json={"args": [], "kwargs": {}}) as resp:
            assert resp.status_code == 200, \
                "stream errors are emitted in-band, not via status code"
            chunks = _read_ndjson_chunks(resp)
        assert chunks[0] == {"data": "first"}
        assert len(chunks) == 2, f"expected exactly 2 chunks, got {len(chunks)}: {chunks}"
        terminal = chunks[1]
        assert "_job_error" in terminal, f"missing _job_error sentinel: {terminal}"
        je = terminal["_job_error"]
        assert je["category"] == "transient", \
            f"CapabilityCancelledError -> transient: got {je['category']}"
        # CapabilityCancelledError-specific recognition signal (used by proxy)
        assert je["original_exc_repr"].startswith("CapabilityCancelledError"), \
            f"original_exc_repr must identify cancellation: {je['original_exc_repr']}"
        assert je["retriable"] is False, \
            "CapabilityCancelledError must NOT be auto-retriable"
        assert je["capability_name"] == "sg52-cancel-capability"
        # JobError.occurred_at serialized via the EnhancedJSONEncoder datetime path
        assert je["occurred_at"] is not None, "occurred_at must serialize"


def test_stream_failure_emits_typed_terminal_chunk():
    app = create_app(STUB_MODULE, "FailingStreamCapability")
    with TestClient(app) as client:
        with client.stream("POST", "/execute_stream",
                           json={"args": [], "kwargs": {}}) as resp:
            assert resp.status_code == 200
            chunks = _read_ndjson_chunks(resp)
        assert chunks[0] == {"data": "before failure"}
        terminal = chunks[1]
        assert "_job_error" in terminal
        je = terminal["_job_error"]
        assert je["category"] == "fatal", f"bare RuntimeError -> fatal: got {je['category']}"
        assert je["retriable"] is False
        assert "simulated stream failure" in je["message"]
        assert je["capability_name"] == "sg52-fail-capability"


# ---------------------------------------------------------------------------
# CR-14: call-envelope propagation into the executor thread
# ---------------------------------------------------------------------------

def test_call_envelope_reaches_executor_thread_and_never_leaks():
    EnvelopeCapability.seen_envelopes = []
    app = create_app(STUB_MODULE, "EnvelopeCapability")
    with TestClient(app) as client:
        # (a) Envelope present: capability code sees exact identity in the
        # executor thread (copy_context carried it across).
        resp = client.post("/execute", json={
            "args": [], "kwargs": {"x": 1},
            "envelope": {"job_id": "job-cr14", "run_id": "run-7",
                         "control": {"force": True},
                         "future_key_ignored": "tolerant"},
        })
        assert resp.status_code == 200, resp.text
        # (b) Envelope absent: contextvar is None (honest unattribution),
        # never a stale carry-over from the previous request.
        resp = client.post("/execute", json={"args": [], "kwargs": {}})
        assert resp.status_code == 200, resp.text
    seen = EnvelopeCapability.seen_envelopes
    assert len(seen) == 2, seen
    assert seen[0] is not None and seen[0].job_id == "job-cr14"
    assert seen[0].run_id == "run-7" and seen[0].control == {"force": True}
    assert seen[1] is None, "envelope must NOT leak across requests"


# ---------------------------------------------------------------------------
# CR-14 follow-up: X-CJM-Accounts response header
# ---------------------------------------------------------------------------

def test_accounts_ride_response_header():
    app = create_app(STUB_MODULE, "AcctRecordingCapability",
                     adapter_specs=[f"{STUB_MODULE}:AcctTaskAdapter"])
    with TestClient(app) as client:
        # (a) Success path: recorded account arrives on the header.
        resp = client.post("/execute", json={"args": [], "kwargs": {"mode": "save"}})
        assert resp.status_code == 200
        accounts = json.loads(resp.headers[ACCOUNTS_HEADER])
        assert accounts == [{"event_type": "result_saved",
                             "payload": {"row_job_id": "row-1",
                                         "text_hash": "sha256:abc"}}]

        # (b) No accounts recorded -> header ABSENT (old-host byte parity).
        resp = client.post("/execute", json={"args": [], "kwargs": {"mode": "plain"}})
        assert resp.status_code == 200
        assert ACCOUNTS_HEADER not in resp.headers

        # (c) Failure path: the save that happened BEFORE the crash still
        # reports on the 500's header beside the _job_error body.
        resp = client.post("/execute",
                           json={"args": [], "kwargs": {"mode": "save_then_fail"}})
        assert resp.status_code == 500
        assert "_job_error" in resp.json()
        accounts = json.loads(resp.headers[ACCOUNTS_HEADER])
        assert accounts[0]["payload"]["row_job_id"] == "row-2"

        # (d) /task: worker-emitted TASK_ACCOUNT, ok=True + duration.
        resp = client.post("/task", json={"task": "acct-task", "method": "do_thing",
                                          "kwargs": {"x": 5}})
        assert resp.status_code == 200 and resp.json() == {"x": 5}
        accounts = json.loads(resp.headers[ACCOUNTS_HEADER])
        assert accounts[0]["event_type"] == "task_account"
        assert accounts[0]["payload"]["ok"] is True
        assert accounts[0]["payload"]["task"] == "acct-task"
        assert "duration_s" in accounts[0]["payload"]

        # (e) /task failure: TASK_ACCOUNT with ok=False + error category.
        resp = client.post("/task", json={"task": "acct-task", "method": "do_thing",
                                          "kwargs": {"x": -1}})
        assert resp.status_code == 500
        accounts = json.loads(resp.headers[ACCOUNTS_HEADER])
        assert accounts[0]["payload"]["ok"] is False
        assert accounts[0]["payload"]["error_category"] == "fatal"

        # (f) No cross-request leakage: a plain call after account-recording
        # calls still carries no header.
        resp = client.post("/execute", json={"args": [], "kwargs": {"mode": "plain"}})
        assert ACCOUNTS_HEADER not in resp.headers
