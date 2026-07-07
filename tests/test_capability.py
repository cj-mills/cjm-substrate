"""ToolCapability tests (projected from nbs/core/capability.ipynb #|hide cells
at the golden-reference flip): Q1-A worker-env template substitution, Q2
heartbeat CM + thread-safe report_progress, SG-44/T28 action dispatch, CR-4
lifecycle helpers + cancellation primitives + the reconfigure two-phase
contract, CR-11 config options, CR-12 EnvVarSpec, the default execute_stream
wrap, and derive_structural_surface.

MinimalCapability is the notebook's _CR4MinimalCapability test scaffold —
it lives HERE now (the notebook had exported a duplicate copy into the
shipped module with zero consumers; dropped at the flip)."""

import dataclasses
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict

import pytest

from cjm_substrate.core.capability import (
    ConfigOption, EnvVarSpec, FieldOptions, RELOAD_TRIGGER, ToolCapability,
    capability_action, collect_capability_actions, derive_structural_surface,
    expand_worker_env_template, template_check_placeholders,
)
from cjm_substrate.core.errors import (
    CapabilityCancelledError, CapabilityConfigError, CapabilityInputError,
)


class MinimalCapability(ToolCapability):
    """Concrete capability satisfying abstracts; relies on CR-4 default cleanup()."""
    @property
    def name(self) -> str: return "cr4-minimal"
    @property
    def version(self) -> str: return "0.0.0"
    def initialize(self, config=None): self._cfg = dict(config or {})
    def execute(self, *args, **kwargs): return None
    def get_config_schema(self) -> Dict[str, Any]: return {}
    def get_current_config(self) -> Dict[str, Any]: return dict(getattr(self, "_cfg", {}))


# ---------------------------------------------------------------------------
# Q1-A: worker-env template substitution
# ---------------------------------------------------------------------------

def test_template_static_defaults_pass_through():
    assert expand_worker_env_template("0", {}) == "0"
    assert expand_worker_env_template("/abs/path", {"CJM_MODELS_DIR": "/foo"}) == "/abs/path"


def test_template_substitutes_placeholders():
    assert expand_worker_env_template(
        "${CJM_MODELS_DIR}/huggingface",
        {"CJM_MODELS_DIR": "/srv/models", "CJM_CAPABILITY_DATA_DIR": None,
         "CAPABILITY_DATA_DIR": None, "CAPABILITY_NAME": "whisper"},
    ) == "/srv/models/huggingface"

    # Multiple placeholders all substituted in one pass.
    assert expand_worker_env_template(
        "${CJM_CAPABILITY_DATA_DIR}/${CAPABILITY_NAME}/nltk_data",
        {"CJM_CAPABILITY_DATA_DIR": "/home/u/.cjm", "CAPABILITY_NAME": "nltk",
         "CJM_MODELS_DIR": None, "CAPABILITY_DATA_DIR": None},
    ) == "/home/u/.cjm/nltk/nltk_data"

    # CAPABILITY_DATA_DIR is the convenience shorthand.
    assert expand_worker_env_template(
        "${CAPABILITY_DATA_DIR}/nltk_data",
        {"CAPABILITY_DATA_DIR": "/home/u/.cjm/data/nltk", "CAPABILITY_NAME": "nltk",
         "CJM_CAPABILITY_DATA_DIR": None, "CJM_MODELS_DIR": None},
    ) == "/home/u/.cjm/data/nltk/nltk_data"


def test_template_is_single_pass_non_recursive():
    # A substituted value containing $-syntax is NOT re-scanned.
    assert expand_worker_env_template(
        "${CJM_MODELS_DIR}/sub",
        {"CJM_MODELS_DIR": "/srv/${FOO}/abc", "CJM_CAPABILITY_DATA_DIR": None,
         "CAPABILITY_DATA_DIR": None, "CAPABILITY_NAME": ""},
    ) == "/srv/${FOO}/abc/sub"


def test_template_unknown_placeholder_raises_with_context():
    with pytest.raises(CapabilityConfigError) as exc_info:
        expand_worker_env_template(
            "${UNKNOWN_VAR}/path",
            {"CJM_MODELS_DIR": "/foo"},
            capability_name="whisper",
            var_name="HF_HOME",
        )
    e = exc_info.value
    msg = str(e)
    assert "UNKNOWN_VAR" in msg, f"missing offending placeholder in msg: {msg}"
    assert "whisper" in msg and "HF_HOME" in msg, f"missing context in msg: {msg}"
    assert e.fields_invalid == ["HF_HOME"], f"fields_invalid: {e.fields_invalid}"


def test_template_missing_value_is_operator_error():
    # Allowed placeholder but unresolved value (None): distinct operator-side
    # message, never a silent empty-string substitution into a path.
    with pytest.raises(CapabilityConfigError) as exc_info:
        expand_worker_env_template(
            "${CJM_MODELS_DIR}/huggingface",
            {"CJM_MODELS_DIR": None, "CJM_CAPABILITY_DATA_DIR": "/foo",
             "CAPABILITY_DATA_DIR": None, "CAPABILITY_NAME": ""},
            capability_name="whisper",
            var_name="HF_HOME",
        )
    msg = str(exc_info.value)
    assert "CJM_MODELS_DIR" in msg
    assert "operator must set" in msg, f"operator-side error context missing: {msg}"


def test_template_check_placeholders_validates_vocabulary():
    # Validate-time helper: returns referenced names, raises on unknowns,
    # needs no values map (cjm-ctl validate's dry-run check).
    assert template_check_placeholders("/abs/path") == set()
    assert template_check_placeholders("${CJM_MODELS_DIR}/foo") == {"CJM_MODELS_DIR"}
    assert template_check_placeholders(
        "${CJM_CAPABILITY_DATA_DIR}/${CAPABILITY_NAME}"
    ) == {"CJM_CAPABILITY_DATA_DIR", "CAPABILITY_NAME"}
    with pytest.raises(CapabilityConfigError):
        template_check_placeholders("${UNKNOWN_VAR}")


# ---------------------------------------------------------------------------
# Q2: thread-safe report_progress + heartbeat context manager
# ---------------------------------------------------------------------------

def test_report_progress_fast_path_without_lock():
    p = MinimalCapability()
    p.report_progress(0.3, "loading")
    assert p._progress == 0.3
    assert p._status_message == "loading"
    assert p._status_message_base == "loading"
    # No lock created in the single-threaded fast path.
    assert getattr(p, "_progress_lock", None) is None


def test_report_progress_uses_lock_inside_heartbeat():
    p = MinimalCapability()
    with p.heartbeat("phase A", interval=2.0):
        assert isinstance(p._progress_lock, type(threading.Lock()))
        old_lock = p._progress_lock
        p.report_progress(0.6, "mid-block update")
        assert p._progress == 0.6
        assert p._status_message_base == "mid-block update"
        assert p._progress_lock is old_lock, "lock identity changed mid-block"


def test_heartbeat_advances_status_tuple():
    # The substrate's stall detector needs the (progress, message) tuple to
    # advance during silent blocking calls.
    p = MinimalCapability()
    samples = []
    with p.heartbeat("loading model", interval=0.05):
        for _ in range(4):
            time.sleep(0.06)
            samples.append(p._status_message)
    assert all("loading model" in s for s in samples), f"base lost: {samples}"
    assert all("(" in s and "s)" in s for s in samples), f"elapsed missing: {samples}"
    assert len(set(samples)) > 1, f"tuple not advancing: {samples}"


def test_heartbeat_preserves_explicit_progress():
    # An explicit report_progress inside the block updates the base; the next
    # heartbeat tick uses the NEW base, not the original phase label.
    p = MinimalCapability()
    with p.heartbeat("loading model", interval=0.05):
        time.sleep(0.06)
        p.report_progress(0.7, "downloading weights")
        time.sleep(0.1)
        final = p._status_message
    assert "downloading weights" in final, f"new base lost: {final}"
    assert "loading model" not in final, f"old base persisted: {final}"
    assert p._progress == 0.7  # heartbeat never touches _progress


def test_heartbeat_thread_terminates_on_both_exits():
    def heartbeat_threads():
        return [t for t in threading.enumerate() if t.name.startswith("heartbeat-")]

    p = MinimalCapability()
    pre_count = len(heartbeat_threads())

    with p.heartbeat("phase A", interval=0.05):
        time.sleep(0.06)
        assert len(heartbeat_threads()) > pre_count, "no heartbeat thread spawned"
    time.sleep(0.2)
    assert len(heartbeat_threads()) == pre_count, \
        "heartbeat thread leaked after normal exit"

    with pytest.raises(RuntimeError, match="simulated block failure"):
        with p.heartbeat("phase B", interval=0.05):
            time.sleep(0.06)
            raise RuntimeError("simulated block failure")
    time.sleep(0.2)
    assert len(heartbeat_threads()) == pre_count, \
        "heartbeat thread leaked after exception exit"


def test_report_progress_safe_under_concurrency():
    # Hammer report_progress from a writer thread while the heartbeat thread
    # re-amends _status_message; the lock keeps the write triple consistent.
    p = MinimalCapability()
    errors = []

    def writer():
        try:
            for i in range(100):
                p.report_progress(i / 100.0, f"update-{i}")
                time.sleep(0.001)
        except Exception as e:
            errors.append(e)

    with p.heartbeat("concurrent test", interval=0.001):
        writer_thread = threading.Thread(target=writer, name="writer")
        writer_thread.start()
        writer_thread.join(timeout=2.0)

    assert not errors, f"concurrent calls raised: {errors}"
    assert p._progress == 99 / 100.0
    assert p._status_message_base == "update-99"


# ---------------------------------------------------------------------------
# SG-44 + T28: action dispatcher convention
# ---------------------------------------------------------------------------

class BaseDispatcher:
    @capability_action("hello")
    def _say_hello(self, **kwargs):
        return "hi"

    @capability_action("goodbye")
    def _say_goodbye(self, **kwargs):
        return "bye"


class ExtendedDispatcher(BaseDispatcher):
    @capability_action("wave")
    def _wave(self, **kwargs):
        return "wave"


def test_capability_action_tags_without_wrapping():
    assert BaseDispatcher._say_hello._capability_action == "hello"
    assert BaseDispatcher()._say_hello() == "hi"  # still callable


def test_collect_capability_actions_walks_mro():
    assert collect_capability_actions(BaseDispatcher) == {"hello", "goodbye"}
    assert collect_capability_actions(ExtendedDispatcher) == {"hello", "goodbye", "wave"}

    class Plain:
        def regular(self): return None
    assert collect_capability_actions(Plain) == set()


class DispatchCapability(MinimalCapability):
    def execute(self, action: str = "ping", **kwargs):
        return self.dispatch_to_action(action, **kwargs)

    @capability_action("ping")
    def _ping(self, **kwargs): return "pong"

    @capability_action("echo")
    def _echo(self, value=None, **kwargs): return value


class ExtendedDispatchCapability(DispatchCapability):
    @capability_action("shout")
    def _shout(self, text="", **kwargs): return text.upper()


def test_dispatch_to_action_routes_and_forwards_kwargs():
    cap = DispatchCapability()
    assert cap.execute("ping") == "pong"
    assert cap.dispatch_to_action("echo", value=42) == 42
    # supported_actions and dispatch share the same markers.
    assert collect_capability_actions(DispatchCapability) == {"ping", "echo"}


def test_dispatch_unknown_action_raises_typed():
    with pytest.raises(CapabilityInputError) as exc_info:
        DispatchCapability().dispatch_to_action("nope")
    assert exc_info.value.fields_invalid == ["action"]


def test_dispatch_walks_mro_for_inherited_handlers():
    cap = ExtendedDispatchCapability()
    assert cap.dispatch_to_action("shout", text="hi") == "HI"
    assert cap.dispatch_to_action("ping") == "pong"
    assert collect_capability_actions(ExtendedDispatchCapability) == \
        {"ping", "echo", "shout"}


# ---------------------------------------------------------------------------
# CR-4: optional hooks, fields_that_changed, reconfigure triggers
# ---------------------------------------------------------------------------

def test_cleanup_and_prefetch_are_optional_no_ops():
    # If cleanup were still @abstractmethod, MinimalCapability (no override)
    # would fail to instantiate.
    p = MinimalCapability()
    p.cleanup()
    p.prefetch()


def test_execute_stream_default_wraps_execute():
    class Echo(MinimalCapability):
        def execute(self, *args, **kwargs): return "result"
    assert list(Echo().execute_stream("x")) == ["result"]


def test_fields_that_changed():
    p = MinimalCapability()
    assert p.fields_that_changed({"a": 1, "b": 2}, {"a": 1, "b": 2}) == set()
    assert p.fields_that_changed({"a": 1}, {"a": 2}) == {"a"}
    assert p.fields_that_changed({"a": 1}, {}) == {"a"}      # only in old
    assert p.fields_that_changed({}, {"a": 1}) == {"a"}      # only in new
    assert p.fields_that_changed({"a": 1, "b": 2}, {"b": 2, "c": 3}) == {"a", "c"}
    # Nested-structure equality is by value.
    assert p.fields_that_changed({"x": [1, 2]}, {"x": [1, 2]}) == set()
    assert p.fields_that_changed({"x": [1, 2]}, {"x": [2, 1]}) == {"x"}


@dataclass
class WhisperTestConfig:
    """Config dataclass with two RELOAD_TRIGGER-tagged fields sharing a trigger."""
    model: str = field(default="base", metadata={RELOAD_TRIGGER: "model"})
    revision: str = field(default="main", metadata={RELOAD_TRIGGER: "model"})  # SAME trigger
    device: str = field(default="cuda", metadata={RELOAD_TRIGGER: "device"})   # DIFFERENT trigger
    temperature: float = field(default=0.0)  # NO trigger


class TriggerCapability(MinimalCapability):
    """Capability that opts into the declarative RELOAD_TRIGGER pattern."""
    config_class = WhisperTestConfig

    def __init__(self):
        self.released = []

    def _release_model(self):
        self.released.append("model")

    def _release_device(self):
        self.released.append("device")


class RaisingTriggerCapability(TriggerCapability):
    def _release_model(self):
        self.released.append("model-attempted")
        raise RuntimeError("simulated release failure")


def test_reconfigure_with_triggers():
    # No change -> no triggers fire.
    p = TriggerCapability()
    p.reconfigure_with_triggers({"model": "base"}, {"model": "base"})
    assert p.released == []

    # Only model changed -> _release_model exactly once.
    p = TriggerCapability()
    p.reconfigure_with_triggers({"model": "base"}, {"model": "large"})
    assert p.released == ["model"]

    # model AND revision changed (same trigger) -> fires ONCE (de-dupe).
    p = TriggerCapability()
    p.reconfigure_with_triggers(
        {"model": "base", "revision": "main"},
        {"model": "large", "revision": "v2"},
    )
    assert p.released == ["model"], f"de-dupe broken: {p.released}"

    # device + model changed -> both fire (set order; assert contents).
    p = TriggerCapability()
    p.reconfigure_with_triggers(
        {"model": "base", "device": "cuda"},
        {"model": "large", "device": "cpu"},
    )
    assert set(p.released) == {"model", "device"}

    # Non-triggered field change -> no release.
    p = TriggerCapability()
    p.reconfigure_with_triggers({"temperature": 0.0}, {"temperature": 0.7})
    assert p.released == []

    # Raising _release is logged + skipped; other triggers still fire.
    p = RaisingTriggerCapability()
    p.reconfigure_with_triggers(
        {"model": "base", "device": "cuda"},
        {"model": "large", "device": "cpu"},
    )
    assert "model-attempted" in p.released
    assert "device" in p.released, "device trigger must fire even after model's raise"

    # Capability without config_class -> silent no-op.
    MinimalCapability().reconfigure_with_triggers({"x": 1}, {"x": 2})


class ApplyConfigCapability(TriggerCapability):
    """Capability with the clean _apply_config seam."""
    def __init__(self):
        super().__init__()
        self.events = []
        self._cfg = {}
    def _release_model(self):
        super()._release_model()
        self.events.append("release:model")
    def _apply_config(self, config):
        self.events.append(("apply", dict(config)))
        self._cfg = dict(config)
    def initialize(self, config=None):
        # must NOT be invoked by reconfigure when _apply_config is present
        self.events.append("initialize")
        self._cfg = dict(config or {})


class FallbackCapability(TriggerCapability):
    """No _apply_config — reconfigure must fall back to initialize(new)."""
    def __init__(self):
        super().__init__()
        self.events = []
        self._cfg = {}
    def _release_model(self):
        super()._release_model()
        self.events.append("release:model")
    def initialize(self, config=None):
        self.events.append(("initialize", dict(config or {})))
        self._cfg = dict(config or {})


def test_reconfigure_two_phase_contract():
    # _apply_config path: release fires THEN apply; initialize() unused.
    p = ApplyConfigCapability()
    p.reconfigure({"model": "base"}, {"model": "large"})
    assert p.events == ["release:model", ("apply", {"model": "large"})], p.events
    assert p.get_current_config() == {"model": "large"}
    assert "initialize" not in p.events

    # Fallback path: release fires THEN initialize(new).
    p = FallbackCapability()
    p.reconfigure({"model": "base"}, {"model": "large"})
    assert p.events == ["release:model", ("initialize", {"model": "large"})], p.events
    assert p.get_current_config() == {"model": "large"}

    # Non-trigger change still applies config (no release fired).
    p = FallbackCapability()
    p.reconfigure({"temperature": 0.0}, {"temperature": 0.7})
    assert p.events == [("initialize", {"temperature": 0.7})], p.events
    assert p.get_current_config() == {"temperature": 0.7}

    # None old_config tolerated; config still applied.
    p = ApplyConfigCapability()
    p.reconfigure(None, {"model": "large"})
    assert p.get_current_config() == {"model": "large"}


def test_reconfigure_delegates_to_triggers():
    p = TriggerCapability()
    p.reconfigure({"model": "base"}, {"model": "large"})
    assert p.released == ["model"], f"reconfigure() must delegate: {p.released}"

    # None args tolerated (default to {}).
    p2 = TriggerCapability()
    p2.reconfigure(None, {"model": "large"})
    assert p2.released == ["model"]


# ---------------------------------------------------------------------------
# CR-11: get_config_options
# ---------------------------------------------------------------------------

def test_config_options_default_is_empty():
    assert MinimalCapability().get_config_options() == {}


def test_config_options_override_and_asdict_round_trip():
    class DynamicCapability(MinimalCapability):
        def get_config_options(self):
            return {
                "model": FieldOptions(
                    options=[
                        ConfigOption("gemini-2.5-flash", "Gemini 2.5 Flash",
                                     {"input_token_limit": 1048576,
                                      "output_token_limit": 65536}),
                        ConfigOption("gemini-2.5-pro", "Gemini 2.5 Pro",
                                     {"input_token_limit": 1048576,
                                      "output_token_limit": 65536}),
                    ],
                    constraints={"max_output_tokens": {"max": 65536}},
                )
            }

    opts = DynamicCapability().get_config_options()
    assert set(opts) == {"model"}
    fo = opts["model"]
    assert isinstance(fo, FieldOptions)
    assert [o.value for o in fo.options] == ["gemini-2.5-flash", "gemini-2.5-pro"]
    assert fo.options[0].metadata["output_token_limit"] == 65536
    # asdict round-trip mirrors the worker's EnhancedJSONEncoder serialization.
    d = {k: dataclasses.asdict(v) for k, v in opts.items()}
    assert d["model"]["options"][1]["label"] == "Gemini 2.5 Pro"
    assert d["model"]["constraints"]["max_output_tokens"]["max"] == 65536


# ---------------------------------------------------------------------------
# CR-12: EnvVarSpec worker-env contract
# ---------------------------------------------------------------------------

def test_env_var_spec_flavors_and_round_trip():
    # Secret flavor: no default (a baked-in secret is a leak).
    secret = EnvVarSpec(name="GEMINI_API_KEY", secret=True, required=True,
                        label="Gemini API Key", description="Google Gemini API key")
    assert secret.secret is True and secret.required is True
    assert secret.default is None, "secret must not carry a default value"

    # Visible flavor: carries a default, resolved via the override chain.
    visible = EnvVarSpec(name="CUDA_VISIBLE_DEVICES", default="0",
                         label="GPU Device", description="Which GPU index the worker uses")
    assert visible.secret is False and visible.default == "0"

    # WORKER_ENV declaration asdict round-trips for the manifest code section.
    class APICapability(MinimalCapability):
        WORKER_ENV = [secret, visible]

    dicts = [dataclasses.asdict(s) for s in APICapability.WORKER_ENV]
    assert dicts[0]["name"] == "GEMINI_API_KEY" and dicts[0]["secret"] is True
    assert dicts[1]["name"] == "CUDA_VISIBLE_DEVICES" and dicts[1]["default"] == "0"

    # No WORKER_ENV declaration is the norm.
    assert getattr(MinimalCapability, "WORKER_ENV", None) is None


# ---------------------------------------------------------------------------
# CR-4: cancellation primitives
# ---------------------------------------------------------------------------

def test_cancel_flag_and_check_cancel():
    p = MinimalCapability()
    assert p._cancel_requested is False
    p.check_cancel()  # no-op while flag unset

    p.cancel()
    assert p._cancel_requested is True

    with pytest.raises(CapabilityCancelledError) as exc_info:
        p.check_cancel()
    assert exc_info.value.capability_name == "cr4-minimal"
    # Must NOT be ValueError-catchable per CR-5 discipline.
    assert not isinstance(exc_info.value, ValueError)

    # Flag stays set after raise (substrate resets it between executions).
    with pytest.raises(CapabilityCancelledError):
        p.check_cancel()


def test_cancel_callbacks_fire_in_order_every_time():
    p = MinimalCapability()
    calls = []
    p.register_cancel_callback(lambda: calls.append("cb1"))
    p.register_cancel_callback(lambda: calls.append("cb2"))

    p.cancel()
    assert calls == ["cb1", "cb2"], f"order broken: {calls}"

    # Subsequent cancel() fires callbacks again.
    p.cancel()
    assert calls == ["cb1", "cb2", "cb1", "cb2"]


def test_misbehaving_cancel_callback_is_skipped():
    p = MinimalCapability()
    calls = []

    def bad_cb(): raise RuntimeError("misbehaving callback")

    p.register_cancel_callback(lambda: calls.append("good"))
    p.register_cancel_callback(bad_cb)
    p.register_cancel_callback(lambda: calls.append("later"))

    p.cancel()
    assert calls == ["good", "later"], f"misbehaving callback blocked others: {calls}"


def test_cancel_signal_to_registers_and_deregisters():
    p = MinimalCapability()
    calls = []

    def teardown_cb(): calls.append("teardown")

    with p.cancel_signal_to(teardown_cb):
        assert teardown_cb in p._cancel_callbacks
        p.cancel()
        assert calls == ["teardown"]
    assert teardown_cb not in p._cancel_callbacks

    # Re-cancel after block: callback no longer fires.
    p.cancel()
    assert calls == ["teardown"], f"deregistered callback fired again: {calls}"

    # Exception path: deregistered even if the with-block raises.
    p2 = MinimalCapability()
    teardown2 = lambda: None
    with pytest.raises(ValueError, match="scoped failure"):
        with p2.cancel_signal_to(teardown2):
            assert teardown2 in p2._cancel_callbacks
            raise ValueError("scoped failure")
    assert teardown2 not in p2._cancel_callbacks, \
        "cancel_signal_to must deregister even when the with-block raises"


# ---------------------------------------------------------------------------
# Structural surface derivation
# ---------------------------------------------------------------------------

def test_derive_structural_surface():
    surf = derive_structural_surface(MinimalCapability)
    names = {m["name"] for m in surf["methods"]}
    assert "execute" in names              # the fused-era capability defines it
    assert "reconfigure" in names          # inherited ToolCapability method
    assert "dispatch_to_action" in names   # (formerly patched-on) members are surface too
    assert "name" in surf["properties"] and "version" in surf["properties"]
    sig = next(m["signature"] for m in surf["methods"] if m["name"] == "initialize")
    assert "config" in sig
    # Deterministic + name-sorted (the canonical-JSON witness hash depends on it).
    assert surf == derive_structural_surface(MinimalCapability)
    assert [m["name"] for m in surf["methods"]] == \
        sorted(m["name"] for m in surf["methods"])
