"""Error-taxonomy regression tests (projected from nbs/core/errors.ipynb cells
test-mro / test-substrate-errors / test-config-error / test-classify /
test-joberror at the golden-reference flip).

The MRO assertions are particularly load-bearing: a future refactor that
accidentally broadened `CapabilityError(ValueError)` would let transient /
resource / fatal errors be caught by a bare `except ValueError:`, silently
widening caller intent — which the taxonomy explicitly forbids (SG-48 dropped
the last ValueError base)."""

import pytest

from cjm_substrate.core.errors import (CapabilityCancelledError, CapabilityConfigError,
                                       CapabilityDisabledError, CapabilityError,
                                       CapabilityFatalError, CapabilityInputError,
                                       CapabilityNotLoadedError, CapabilityResourceError,
                                       CapabilityTimeoutError, CapabilityTransientError,
                                       classify_exception, JobError,
                                       map_bare_exception_to_job_error, ResourceShortfall,
                                       TracebackPolicy, WorkerOOMError)


def test_mro_discipline_no_capability_error_extends_valueerror():
    input_err = CapabilityInputError("bad", fields_invalid=["foo"])
    assert not isinstance(input_err, ValueError), "SG-48 dropped the ValueError base"
    assert isinstance(input_err, CapabilityError)
    assert input_err.category == 'user_input'
    assert input_err.default_retriable is True
    assert input_err.fields_invalid == ["foo"]

    transient_err = CapabilityTransientError("slow", retry_after_seconds=5.0)
    assert not isinstance(transient_err, ValueError), \
        "CapabilityTransientError must NOT inherit ValueError (semantic discipline)"
    assert transient_err.category == 'transient'
    assert transient_err.retry_after_seconds == 5.0

    resource_err = CapabilityResourceError(
        "oom",
        resource_shortfall=ResourceShortfall(resource='gpu_vram_mb',
                                             needed=8000, available=4000))
    assert not isinstance(resource_err, ValueError)
    assert resource_err.category == 'resource'
    assert resource_err.resource_shortfall.needed == 8000

    fatal_err = CapabilityFatalError("crashed")
    assert not isinstance(fatal_err, ValueError)
    assert fatal_err.category == 'fatal'
    assert fatal_err.default_retriable is False


def test_substrate_typed_exceptions_anchor_under_the_right_category():
    disabled = CapabilityDisabledError("whisper")
    assert isinstance(disabled, CapabilityInputError)
    assert not isinstance(disabled, ValueError)
    assert disabled.category == 'user_input'
    assert disabled.capability_name == "whisper"

    not_loaded = CapabilityNotLoadedError("whisper")
    assert isinstance(not_loaded, CapabilityFatalError)
    assert not isinstance(not_loaded, ValueError), \
        "CapabilityNotLoadedError must NOT be catchable as ValueError (it's a fatal bug)"
    assert not_loaded.category == 'fatal'

    timeout = CapabilityTimeoutError("whisper", timeout_seconds=30.0,
                                     retry_after_seconds=60.0)
    assert isinstance(timeout, CapabilityTransientError)
    assert timeout.category == 'transient'
    assert timeout.timeout_seconds == 30.0
    assert timeout.retry_after_seconds == 60.0

    # CR-4: cancellation is transient (in-principle re-runnable) but non-retriable
    # (deliberate operator action — the substrate must not auto-retry it)
    cancelled = CapabilityCancelledError("whisper")
    assert isinstance(cancelled, CapabilityTransientError)
    assert not isinstance(cancelled, ValueError), \
        "cancellation is a control-flow signal, not a value error"
    assert cancelled.category == 'transient'
    assert cancelled.default_retriable is False
    assert cancelled.capability_name == "whisper"
    assert "cancelled by operator" in str(cancelled)


def test_worker_oom_error_is_the_track_a_resource_signal():
    # CR-7 Track A: worker died from SIGKILL; substrate knows returncode, not shortfall
    oom = WorkerOOMError("whisper", process_returncode=-9)
    assert isinstance(oom, CapabilityResourceError), "must catch under CapabilityResourceError"
    assert not isinstance(oom, ValueError)
    assert oom.category == 'resource', "CR-7 reactive retry dispatches on category=resource"
    assert oom.default_retriable is True, "OOM is retriable after eviction (the point of CR-7)"
    assert oom.capability_name == "whisper"
    assert oom.process_returncode == -9
    assert oom.resource_shortfall is None, \
        "Track A: substrate doesn't know needed/available; only Track B does"
    assert "whisper" in str(oom) and "returncode=-9" in str(oom)

    # Track A + Track B converge at the CapabilityResourceError catch-point
    with pytest.raises(CapabilityResourceError):
        raise WorkerOOMError("voxtral", process_returncode=-9)
    with pytest.raises(CapabilityResourceError):
        raise CapabilityResourceError(
            "voxtral: CUDA OOM",
            resource_shortfall=ResourceShortfall(resource='gpu_vram_mb',
                                                 needed=24000, available=8000))

    oom_custom = WorkerOOMError("whisper", message="custom diagnostic")
    assert str(oom_custom) == "custom diagnostic"
    assert oom_custom.process_returncode is None


def test_config_error_reparented_with_canonical_fields_invalid():
    err = CapabilityConfigError("unknown keys", fields_invalid=["foo", "bar"],
                                config_class_name="WhisperConfig")
    assert isinstance(err, CapabilityInputError)
    assert not isinstance(err, ValueError), "SG-48 dropped the ValueError base"
    assert err.fields_invalid == ["foo", "bar"]
    assert err.config_class_name == "WhisperConfig"


def test_classify_exception_defaults():
    assert classify_exception(ValueError("bad")) == 'user_input'
    assert classify_exception(TypeError("bad")) == 'user_input'
    assert classify_exception(FileNotFoundError("missing")) == 'user_input'
    assert classify_exception(TimeoutError("slow")) == 'transient'
    assert classify_exception(ConnectionError("net")) == 'transient'
    assert classify_exception(MemoryError("oom")) == 'resource'
    assert classify_exception(RuntimeError("unknown")) == 'fatal'

    # CapabilityError subclasses report their own DECLARED category — never a
    # builtin-derived one (post-SG-48 they share no builtin base anyway)
    assert classify_exception(CapabilityInputError("x")) == 'user_input'
    assert classify_exception(CapabilityTransientError("x")) == 'transient'
    assert classify_exception(CapabilityResourceError("x")) == 'resource'
    assert classify_exception(CapabilityFatalError("x")) == 'fatal'
    assert classify_exception(CapabilityNotLoadedError("whisper")) == 'fatal'


def test_map_bare_exception_to_job_error_structured_data_and_policy():
    try:
        raise CapabilityConfigError("bad config", fields_invalid=["model"])
    except Exception as e:
        err = map_bare_exception_to_job_error(e, capability_name="whisper")
    assert isinstance(err, JobError)
    assert err.category == 'user_input' and err.retriable is True
    assert err.fields_invalid == ["model"]
    assert err.capability_name == "whisper"
    assert err.traceback is not None and "CapabilityConfigError" in err.traceback
    # CR-5 Python 3.12+ form: occurred_at must be timezone-aware
    assert err.occurred_at is not None and err.occurred_at.tzinfo is not None

    try:
        raise CapabilityResourceError(
            "oom", resource_shortfall=ResourceShortfall(resource='gpu_vram_mb',
                                                        needed=16000, available=8000))
    except Exception as e:
        err = map_bare_exception_to_job_error(e)
    assert err.category == 'resource' and err.resource_shortfall.needed == 16000

    try:
        raise ValueError("unmapped bare")
    except Exception as e:
        err = map_bare_exception_to_job_error(e)
    assert err.category == 'user_input' and err.retriable is True
    assert err.fields_invalid is None  # bare ValueError has no fields_invalid

    try:
        raise RuntimeError("unknown")
    except Exception as e:
        err = map_bare_exception_to_job_error(e)
    assert err.category == 'fatal' and err.retriable is False

    # TracebackPolicy.NONE suppresses traceback + message; repr survives
    try:
        raise ValueError("secret")
    except Exception as e:
        err = map_bare_exception_to_job_error(e, traceback_policy=TracebackPolicy.NONE)
    assert err.traceback is None and err.message == ""
    assert err.original_exc_repr
