"""RemoteCapabilityProxy tests (projected from nbs/core/proxy.ipynb #|hide
cells at the golden-reference flip): CR-14 follow-up account harvest, the G7
unary typed-error contract, CR-7 Track A worker-death classification, and the
SG-52 _job_error chunk-to-typed-exception mapper.

Stubs borrow methods from RemoteCapabilityProxy itself (not module-level
names) so the tests survive the de-scar fold; the notebook's scar-wiring
identity asserts (RemoteCapabilityProxy.execute is execute_with_oom_check)
became inspect.getsource wiring checks for the same reason."""

import inspect
import json
import signal

import pytest

from cjm_substrate.core.errors import (
    CapabilityCancelledError, CapabilityError, CapabilityFatalError,
    CapabilityInputError, CapabilityResourceError, CapabilityTransientError,
    WorkerOOMError,
)
from cjm_substrate.core.proxy import (
    RemoteCapabilityProxy, _raise_from_job_error_chunk,
    _raise_typed_execute_error,
)
from cjm_substrate.core.wire import (
    ACCOUNTS_HEADER, CallEnvelope, reset_call_envelope, set_call_envelope,
)


# ---------------------------------------------------------------------------
# CR-14 follow-up: _harvest_worker_accounts
# ---------------------------------------------------------------------------

class ListJournal:
    def __init__(self):
        self.rows = []

    def append(self, ev):
        self.rows.append(ev)
        return len(self.rows)


class HarvestStubProxy:
    name = "stub-capability"
    worker_session_id = "ws-h1"
    def __init__(self):
        self.journal = ListJournal()
    _harvest_worker_accounts = RemoteCapabilityProxy._harvest_worker_accounts


class HeaderResponse:
    def __init__(self, headers):
        self.headers = headers


def test_harvest_absent_header_is_noop():
    proxy = HarvestStubProxy()
    proxy._harvest_worker_accounts(HeaderResponse({}))
    assert proxy.journal.rows == []


def test_harvest_journals_accounts_with_envelope_identity():
    proxy = HarvestStubProxy()
    tok = set_call_envelope(CallEnvelope(job_id="j-h", run_id="r-h", actor="cli:t"))
    try:
        proxy._harvest_worker_accounts(HeaderResponse({ACCOUNTS_HEADER: json.dumps([
            {"event_type": "result_saved", "payload": {"row_job_id": "row-9"}},
            {"event_type": "task_account", "payload": {"task": "t", "ok": True}},
        ])}))
    finally:
        reset_call_envelope(tok)
    assert len(proxy.journal.rows) == 2
    r0 = proxy.journal.rows[0]
    assert r0.event_type == "result_saved" and r0.worker_reported is True
    assert r0.job_id == "j-h" and r0.run_id == "r-h" and r0.actor == "cli:t"
    assert r0.capability_name == "stub-capability"
    assert r0.worker_session_id == "ws-h1"
    assert r0.payload == {"row_job_id": "row-9"}


def test_harvest_envelope_less_rows_stay_unattributed():
    proxy = HarvestStubProxy()
    proxy._harvest_worker_accounts(HeaderResponse({ACCOUNTS_HEADER: json.dumps([
        {"event_type": "cache_hit", "payload": {}}])}))
    assert proxy.journal.rows[0].job_id is None
    assert proxy.journal.rows[0].worker_reported is True


def test_harvest_malformed_header_never_raises():
    proxy = HarvestStubProxy()
    proxy._harvest_worker_accounts(HeaderResponse({ACCOUNTS_HEADER: "{not json"}))
    assert proxy.journal.rows == []


# ---------------------------------------------------------------------------
# G7: the unary execute error contract (_raise_typed_execute_error)
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, body, text="boom"):
        self._body = body
        self.text = text

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def test_unary_resource_job_error_raises_typed():
    # Post-fix workers return {"_job_error": ...} as the 500 body -> the proxy
    # raises the TYPED exception (what CR-7 retry catches).
    with pytest.raises(CapabilityResourceError, match="CUDA OOM"):
        _raise_typed_execute_error(FakeResponse({"_job_error": {
            "category": "resource", "message": "CUDA OOM loading model",
            "resource_shortfall": {"resource": "gpu_memory_mb", "needed": 48000.0,
                                   "available": 23500.0},
        }}), "fake-capability")


def test_unary_fatal_job_error_raises_typed():
    with pytest.raises(CapabilityFatalError):
        _raise_typed_execute_error(FakeResponse({"_job_error": {
            "category": "fatal", "message": "model file corrupt"}}), "fake-capability")


def test_unary_prefix_worker_falls_back_to_runtime_error():
    # Pre-fix workers return a bare-string detail (version-skew tolerance).
    with pytest.raises(RuntimeError, match="Execute failed"):
        _raise_typed_execute_error(
            FakeResponse({"detail": "boom"}, text="boom"), "fake-capability")


def test_unary_non_json_body_falls_back_to_runtime_error():
    with pytest.raises(RuntimeError, match="Execute failed"):
        _raise_typed_execute_error(
            FakeResponse(ValueError("not json"), text="<html>"), "fake-capability")


# ---------------------------------------------------------------------------
# CR-7 Track A: _check_worker_death classification
# ---------------------------------------------------------------------------

class StubProcess:
    def __init__(self, returncode):
        self._rc = returncode
    def poll(self):
        return self._rc


class DeathStubProxy:
    name = "stub-capability"
    def __init__(self, process):
        self.process = process
    _check_worker_death = RemoteCapabilityProxy._check_worker_death


def test_worker_alive_returns_silently():
    DeathStubProxy(StubProcess(returncode=None))._check_worker_death()


def test_worker_already_cleaned_up_returns_silently():
    DeathStubProxy(None)._check_worker_death()


def test_sigkill_death_classifies_as_oom():
    sigkill_rc = -getattr(signal, "SIGKILL", 9)
    with pytest.raises(WorkerOOMError) as exc_info:
        DeathStubProxy(StubProcess(returncode=sigkill_rc))._check_worker_death()
    e = exc_info.value
    assert e.capability_name == "stub-capability"
    assert e.process_returncode == sigkill_rc
    assert isinstance(e, CapabilityResourceError), \
        "WorkerOOMError must catch under CapabilityResourceError (CR-7 reactive retry site)"


def test_sigsegv_death_classifies_as_transient():
    with pytest.raises(CapabilityTransientError) as exc_info:
        DeathStubProxy(StubProcess(returncode=-11))._check_worker_death()
    assert not isinstance(exc_info.value, WorkerOOMError), \
        "non-SIGKILL deaths must NOT classify as WorkerOOMError"
    assert "returncode=-11" in str(exc_info.value)


def test_exit_code_death_classifies_as_transient():
    # Capability crashed during __init__ or some other non-OOM cause.
    with pytest.raises(CapabilityTransientError):
        DeathStubProxy(StubProcess(returncode=1))._check_worker_death()


def test_execute_paths_carry_track_a_check():
    # The notebook asserted scar wiring by identity (RemoteCapabilityProxy.
    # execute is execute_with_oom_check); after the de-scar fold the wrapper
    # IS the method, so assert the invariant that matters: every execute-ish
    # path routes httpx faults through the Track-A classifier.
    for meth in (RemoteCapabilityProxy.execute, RemoteCapabilityProxy.execute_async,
                 RemoteCapabilityProxy.execute_task, RemoteCapabilityProxy.execute_task_async):
        assert "_check_worker_death" in inspect.getsource(meth), \
            f"{meth.__name__} must classify worker death on transport faults"
    assert hasattr(RemoteCapabilityProxy, "_check_worker_death")


# ---------------------------------------------------------------------------
# SG-52: _raise_from_job_error_chunk category dispatch
# ---------------------------------------------------------------------------

def test_chunk_cancellation_special_case():
    # CapabilityCancelledError via original_exc_repr prefix (transient
    # category alone doesn't capture the non-retriable semantic).
    with pytest.raises(CapabilityCancelledError) as exc_info:
        _raise_from_job_error_chunk(
            {"category": "transient", "message": "cancelled",
             "original_exc_repr": "CapabilityCancelledError('whisper cancelled')"},
            capability_name="whisper",
        )
    assert exc_info.value.capability_name == "whisper"
    assert not isinstance(exc_info.value, ValueError), \
        "CapabilityCancelledError must NOT be ValueError-catchable"


def test_chunk_user_input_category():
    with pytest.raises(CapabilityInputError) as exc_info:
        _raise_from_job_error_chunk(
            {"category": "user_input", "message": "bad config",
             "original_exc_repr": "CapabilityConfigError(...)",
             "fields_invalid": ["model"]},
            capability_name="whisper",
        )
    e = exc_info.value
    assert not isinstance(e, ValueError), "SG-48 dropped the ValueError base"
    assert e.fields_invalid == ["model"]
    assert "bad config" in str(e)


def test_chunk_transient_category():
    with pytest.raises(CapabilityTransientError) as exc_info:
        _raise_from_job_error_chunk(
            {"category": "transient", "message": "network blip",
             "original_exc_repr": "TimeoutError(...)", "retry_after_seconds": 5.0},
            capability_name="whisper",
        )
    assert not isinstance(exc_info.value, CapabilityCancelledError), \
        "non-cancellation transient must not be a CapabilityCancelledError"
    assert exc_info.value.retry_after_seconds == 5.0


def test_chunk_resource_category_with_shortfall():
    with pytest.raises(CapabilityResourceError) as exc_info:
        _raise_from_job_error_chunk(
            {"category": "resource", "message": "oom",
             "original_exc_repr": "CapabilityResourceError(...)",
             "resource_shortfall": {"resource": "gpu_vram_mb", "needed": 8000.0,
                                    "available": 4000.0}},
            capability_name="whisper",
        )
    shortfall = exc_info.value.resource_shortfall
    assert shortfall is not None
    assert shortfall.needed == 8000.0
    assert shortfall.resource == "gpu_vram_mb"


def test_chunk_resource_category_without_shortfall():
    with pytest.raises(CapabilityResourceError) as exc_info:
        _raise_from_job_error_chunk(
            {"category": "resource", "message": "vague resource issue",
             "original_exc_repr": "CapabilityResourceError(...)"},
            capability_name="whisper",
        )
    assert exc_info.value.resource_shortfall is None


def test_chunk_fatal_category():
    with pytest.raises(CapabilityFatalError) as exc_info:
        _raise_from_job_error_chunk(
            {"category": "fatal", "message": "bug",
             "original_exc_repr": "RuntimeError(...)"},
            capability_name="whisper",
        )
    assert exc_info.value.category == "fatal"
    assert exc_info.value.default_retriable is False


def test_chunk_unknown_category_is_forensic_runtime_error():
    with pytest.raises(RuntimeError, match="weird_category") as exc_info:
        _raise_from_job_error_chunk(
            {"category": "weird_category", "message": "x",
             "original_exc_repr": "..."},
            capability_name="whisper",
        )
    assert not isinstance(exc_info.value, CapabilityError), \
        "unknown category must NOT raise a CapabilityError subclass"


def test_initialize_routes_through_stall_detection(monkeypatch):
    """555159cd: initialize is where a capability's model load runs, so it must
    ride the progress-based stall detector, not a fixed HTTP deadline (the old
    bare httpx.Client() carried the 5s default read timeout — a cold FA model
    load blew it twice). A False return (worker unreachable) raises loudly
    instead of being swallowed by the load path."""
    import cjm_substrate.core.proxy as proxy_mod

    calls = []

    def fake_stall_post(proxy, threshold, poll_interval_seconds, endpoint=None, payload=None):
        calls.append((endpoint, payload))
        return True

    monkeypatch.setattr(proxy_mod, "_run_prefetch_with_stall_detection", fake_stall_post)
    monkeypatch.setattr(proxy_mod, "_resolve_prefetch_stall_threshold", lambda: 60.0)

    p = object.__new__(RemoteCapabilityProxy)  # no worker spawn
    RemoteCapabilityProxy.initialize(p, {"model": "x"})
    assert calls == [("initialize", {"model": "x"})]

    monkeypatch.setattr(proxy_mod, "_run_prefetch_with_stall_detection", lambda *a, **k: False)
    with pytest.raises(RuntimeError):
        RemoteCapabilityProxy.initialize(p, {"model": "x"})


def test_stall_detector_post_carries_no_fixed_deadline(monkeypatch):
    """The stall detector's lifecycle POST runs with timeout=None — /progress
    polling is the bound, never a wall-clock deadline — and forwards the
    parameterized endpoint + JSON payload (555159cd generalization)."""
    import cjm_substrate.core.proxy as proxy_mod

    seen = {}

    class _StubResp:
        status_code = 200

    class _StubClient:
        def __init__(self, timeout="MISSING", **kw):
            seen.setdefault("timeout", timeout)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None):
            seen["url"] = url
            seen["json"] = json
            return _StubResp()

        def get(self, url):
            return _StubResp()

    monkeypatch.setattr(proxy_mod.httpx, "Client", _StubClient)

    class _StubP:
        base_url = "http://127.0.0.1:9"
        name = "stub"
        process = None

    # poll_interval > POST duration so the join sees the thread finish first
    ok = proxy_mod._run_prefetch_with_stall_detection(
        _StubP(), 60.0, 5.0, endpoint="initialize", payload={"model": "x"})
    assert ok is True
    assert seen["timeout"] is None
    assert seen["url"].endswith("/initialize")
    assert seen["json"] == {"model": "x"}
