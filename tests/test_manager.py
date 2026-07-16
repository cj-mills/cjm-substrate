"""CapabilityManager tests (projected from nbs/core/manager.ipynb hide cells
85ff8c12 (SG-17 binding) / 914196e1 (Phase 5a platform query) / 1edcc449 (CR-2
persistence + hooks) / dd62cbb2 (CR-3 typed stats) / e07fad71 (SG-5 validate) /
546e7068 (CR-10 multi-instance) / 6606dc15 (CR-10b concurrent) / c3c38690 (CR-7
reactive retry + SG-33) / acb13d82 (CR-12 worker-env) / gpu-subtree-attr-test
at the golden-reference flip). `_CR10StubProxy` moved here from the module's
exports (test scaffold, no production consumers)."""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

from cjm_substrate.core import platform as substrate_platform
from cjm_substrate.core.config_store import LocalCapabilityConfigStore
from cjm_substrate.core.empirical_store import LocalEmpiricalResourceStore
from cjm_substrate.core.errors import (CapabilityDisabledError, CapabilityResourceError,
                                       ResourceShortfall, WorkerOOMError)
from cjm_substrate.core.manager import CapabilityBinding, CapabilityManager
from cjm_substrate.core.manifest_format import CodeSection, ManifestV2
from cjm_substrate.core.metadata import (CapabilityInstance, CapabilityLoadSpec,
                                         CapabilityMeta, ResourceRequirements)
from cjm_substrate.core.scheduling import PermissiveScheduler
from cjm_substrate.utils.validation import CapabilityConfigError


class _CR10StubProxy:
    """Stand-in proxy tracking execute calls + hook fires for verification."""
    def __init__(self, name="stub"):
        self._name = name
        self.execute_calls = []
        self.on_disable_calls = 0
        self.on_enable_calls = 0
    @property
    def name(self): return self._name
    @property
    def version(self): return "0.0.1"
    def initialize(self, config): self._config = dict(config or {})
    def execute(self, *args, **kwargs):
        self.execute_calls.append((args, kwargs))
        return {"who": self._name, "args": args, "kwargs": kwargs}
    def get_config_schema(self): return {}
    def get_current_config(self): return {}
    def cleanup(self): pass
    def on_disable(self): self.on_disable_calls += 1
    def on_enable(self): self.on_enable_calls += 1


# ─── SG-17: CapabilityBinding ────────────────────────────────────────────────

def test_sg17_binding_forwards_to_manager():
    """CapabilityBinding forwards to a stub manager with capability_name
    pre-supplied; exercises the binding without spawning workers."""
    pm_stub = MagicMock(spec=CapabilityManager)
    pm_stub.execute_capability.return_value = "ok"
    pm_stub.get_capability.return_value = object()  # is_loaded True
    pm_stub.get_capability_meta.return_value = CapabilityMeta(name="whisper", version="1.0.0")

    binding = CapabilityBinding(manager=pm_stub, capability_name="whisper",
                                default_config={"model": "base"})

    # Observation forwards
    assert binding.capability_name == "whisper"
    assert binding.is_loaded is True
    assert binding.meta.name == "whisper"

    # Execution forwards capability_name automatically
    assert binding.execute(audio="x") == "ok"
    pm_stub.execute_capability.assert_called_once_with("whisper", audio="x")

    # update_config / reload / unload / enable / disable all forward
    binding.update_config({"model": "large"})
    pm_stub.update_capability_config.assert_called_once_with(
        "whisper", {"model": "large"}, strict=True,
    )
    binding.reload({"model": "large"})
    pm_stub.reload_capability.assert_called_once_with("whisper", {"model": "large"})
    binding.unload()
    pm_stub.unload_capability.assert_called_once_with("whisper")
    binding.enable()
    binding.disable()
    pm_stub.enable_capability.assert_called_once_with("whisper")
    pm_stub.disable_capability.assert_called_once_with("whisper")

    # load() uses default_config when no override
    pm_stub.get_discovered_meta.return_value = CapabilityMeta(name="whisper", version="1.0.0")
    pm_stub.load_capability.return_value = True
    assert binding.load() is True
    _, args, kwargs = pm_stub.load_capability.mock_calls[-1]
    assert args[1] == {"model": "base"}, f"expected default_config, got {args[1]}"


def test_sg17_bind_returns_binding_with_defensive_copy():
    pm = CapabilityManager.__new__(CapabilityManager)
    pm.logger = logging.getLogger("test_sg17_bind")
    b = pm.bind("whisper", default_config={"model": "tiny"})
    assert isinstance(b, CapabilityBinding)
    assert b.manager is pm
    assert b.capability_name == "whisper"
    assert b.default_config == {"model": "tiny"}

    # Mutating the caller's dict doesn't bleed into the binding
    caller_cfg = {"model": "tiny"}
    b2 = pm.bind("whisper", default_config=caller_cfg)
    caller_cfg["model"] = "large"
    assert b2.default_config == {"model": "tiny"}


# ─── Phase 5a: platform query ────────────────────────────────────────────────

def test_phase5a_platform_query_filters(monkeypatch):
    """get_compatible_for_current_platform filters a synthetic discovered set;
    empty/absent resources means universal compatibility."""
    pm = CapabilityManager.__new__(CapabilityManager)
    pm.logger = logging.getLogger("test_phase5a")
    pm.discovered = [
        CapabilityMeta(name="whisper-local", version="1.0.0",
                       resources=ResourceRequirements(
                           requires_gpu=True, platforms=["linux-x64"], accelerators=["cuda"])),
        CapabilityMeta(name="voxtral-hf", version="1.0.0",
                       resources=ResourceRequirements(
                           requires_gpu=True, platforms=["linux-x64", "darwin-arm64"],
                           accelerators=["cuda", "mps"])),
        CapabilityMeta(name="qwen3-fa", version="1.0.0", resources=None),
        CapabilityMeta(name="sqlite-graph", version="1.0.0",
                       resources=ResourceRequirements()),  # empty platforms = universal
        CapabilityMeta(name="legacy-capability", version="0.0.1", resources=None),
    ]

    monkeypatch.setattr(substrate_platform, "get_current_platform", lambda: "linux-x64")
    names = {m.name for m in pm.get_compatible_for_current_platform()}
    assert names == {"whisper-local", "voxtral-hf", "qwen3-fa",
                     "sqlite-graph", "legacy-capability"}

    # darwin-arm64: whisper-local (linux-only) drops out
    monkeypatch.setattr(substrate_platform, "get_current_platform", lambda: "darwin-arm64")
    names = {m.name for m in pm.get_compatible_for_current_platform()}
    assert names == {"voxtral-hf", "qwen3-fa", "sqlite-graph", "legacy-capability"}


# ─── CR-2: persistence + lifecycle hooks ─────────────────────────────────────

class _StubCapabilityInstance:
    """Minimal stand-in for a worker capability proxy. Records hook calls + holds config."""
    def __init__(self, name="stub"):
        self._name = name
        self._config: Dict[str, Any] = {}
        self.on_disable_calls = 0
        self.on_enable_calls = 0
    @property
    def name(self): return self._name
    @property
    def version(self): return "0.0.1"
    def initialize(self, config): self._config = dict(config or {})
    def execute(self, *a, **kw): return None
    def get_config_schema(self): return {}
    def get_current_config(self): return dict(self._config)
    def cleanup(self): pass
    def on_disable(self): self.on_disable_calls += 1
    def on_enable(self): self.on_enable_calls += 1


def test_cr2_persistence_hooks_deferred_disable(tmp_path):
    """CapabilityConfigStore persistence + enable/disable hooks + deferred-on-disable
    semantics + CapabilityDisabledError raise sites (CR-10-compat setup)."""
    db_path = tmp_path / "capability_configs.db"
    store = LocalCapabilityConfigStore(db_path)
    pm = CapabilityManager.__new__(CapabilityManager)
    pm.capabilities = {}
    pm.instances = {}
    pm.discovered = []
    pm.logger = logging.getLogger("test_cr2")
    pm.scheduler = PermissiveScheduler()
    pm.config_store = store
    pm._running_executions = set()
    pm._pending_disable_hooks = set()

    stub = _StubCapabilityInstance(name="whisper")
    meta = CapabilityMeta(name="whisper", version="1.0.0")
    meta.instance = stub
    pm.capabilities["whisper"] = meta
    pm.instances["whisper"] = CapabilityInstance(
        instance_id="whisper", capability_name="whisper", proxy=stub)

    # disable: persists + fires on_disable when no in-flight job
    assert pm.disable_capability("whisper") is True
    assert meta.enabled is False
    assert pm.instances["whisper"].enabled is False
    assert stub.on_disable_calls == 1, "on_disable should fire immediately when no in-flight job"
    persisted = LocalCapabilityConfigStore(db_path).get("whisper")
    assert persisted is not None and persisted.enabled is False

    # Idempotent disable: no second hook fire
    assert pm.disable_capability("whisper") is True
    assert stub.on_disable_calls == 1

    # enable: persists + fires on_enable on state change
    assert pm.enable_capability("whisper") is True
    assert meta.enabled is True
    assert pm.instances["whisper"].enabled is True
    assert stub.on_enable_calls == 1
    assert LocalCapabilityConfigStore(db_path).get("whisper").enabled is True

    # Idempotent enable
    assert pm.enable_capability("whisper") is True
    assert stub.on_enable_calls == 1

    # deferred on_disable while in-flight job is running
    stub.on_disable_calls = 0
    pm._running_executions.add("whisper")
    assert pm.disable_capability("whisper") is True
    assert meta.enabled is False
    assert stub.on_disable_calls == 0, "on_disable must be deferred while job in flight"
    assert "whisper" in pm._pending_disable_hooks
    pm._running_executions.discard("whisper")
    pm._maybe_fire_disable_hook("whisper")
    assert stub.on_disable_calls == 1, "deferred on_disable must fire after job finishes"
    assert "whisper" not in pm._pending_disable_hooks

    # CapabilityDisabledError raised at execute time (still disabled)
    try:
        pm.execute_capability("whisper")
    except CapabilityDisabledError as e:
        assert e.capability_name == "whisper"
    else:
        raise AssertionError("execute on disabled capability must raise CapabilityDisabledError")


# ─── CR-3: typed system-monitor stats ────────────────────────────────────────

class _TypedStubMonitor:
    """Stub with CR-3 typed surface. get_system_status returns a dict."""
    def __init__(self, stats):
        self._stats = stats
        self.execute_calls = 0
    def get_system_status(self):
        return self._stats
    def execute(self, command, **kwargs):
        self.execute_calls += 1
        return {"should_not_be_called": True}


class _ToDictStubMonitor:
    """Typed call returns an object with .to_dict() (in-process SystemStats)."""
    class _Bag:
        def to_dict(self):
            return {"cpu_percent": 12.5, "gpu_type": "AMD"}
    def get_system_status(self):
        return self._Bag()


class _NoneReturningMonitor:
    """Proxy returned None (ConnectError) — substrate should degrade silently."""
    def get_system_status(self):
        return None


class _RaisingTypedMonitor:
    """Typed call raises; substrate logs + returns empty (no dispatcher fallback)."""
    def get_system_status(self):
        raise RuntimeError("typed call boom")


class _NoTypedSurfaceMonitor:
    """Misconfigured monitor lacking get_system_status. Substrate degrades to {} +
    warns (the pre-CR-3 execute() dispatcher fallback was removed at SG-48)."""
    def __init__(self):
        self.execute_calls = 0
    def execute(self, command, **kwargs):
        self.execute_calls += 1  # Must never be called — the dispatcher fallback is gone.
        return {"should_not_be_called": True}


def test_cr3_global_stats_typed_paths():
    pm = CapabilityManager.__new__(CapabilityManager)
    pm.logger = logging.getLogger("test_cr3_global_stats")

    # Typed monitor returns dict → returned as-is, dispatcher not called
    typed = _TypedStubMonitor({"cpu_percent": 42.0, "gpu_type": "NVIDIA"})
    pm.system_monitor = typed
    assert pm._get_global_stats() == {"cpu_percent": 42.0, "gpu_type": "NVIDIA"}
    assert typed.execute_calls == 0

    # to_dict()-able object → wrapped via to_dict()
    pm.system_monitor = _ToDictStubMonitor()
    assert pm._get_global_stats() == {"cpu_percent": 12.5, "gpu_type": "AMD"}

    # None (proxy ConnectError path) → {} silently
    pm.system_monitor = _NoneReturningMonitor()
    assert pm._get_global_stats() == {}

    # Typed raises → {} (warning logged; no dispatcher fallback)
    pm.system_monitor = _RaisingTypedMonitor()
    assert pm._get_global_stats() == {}

    # No typed surface → {} + warning; removed dispatcher fallback must not fire
    no_typed = _NoTypedSurfaceMonitor()
    pm.system_monitor = no_typed
    assert pm._get_global_stats() == {}
    assert no_typed.execute_calls == 0

    # No monitor configured → {}
    pm.system_monitor = None
    assert pm._get_global_stats() == {}


class _AsyncTypedStubMonitor:
    """Async-typed surface — exposes get_system_status_async."""
    def __init__(self, stats):
        self._stats = stats
        self.execute_async_calls = 0
    async def get_system_status_async(self):
        return self._stats
    async def execute_async(self, command, **kwargs):
        self.execute_async_calls += 1
        return {"should_not_be_called": True}


class _NoAsyncTypedSurfaceMonitor:
    """Async monitor lacking get_system_status_async → {} + warns (the
    execute_async() dispatcher fallback was removed at SG-48)."""
    def __init__(self):
        self.execute_async_calls = 0
    async def execute_async(self, command, **kwargs):
        self.execute_async_calls += 1  # Must never be called.
        return {"should_not_be_called": True}


def test_cr3_global_stats_async_typed_paths():
    pm = CapabilityManager.__new__(CapabilityManager)
    pm.logger = logging.getLogger("test_cr3_global_stats_async")

    async def _run():
        typed = _AsyncTypedStubMonitor({"cpu_percent": 77.0})
        pm.system_monitor = typed
        assert await pm._get_global_stats_async() == {"cpu_percent": 77.0}
        assert typed.execute_async_calls == 0

        no_typed = _NoAsyncTypedSurfaceMonitor()
        pm.system_monitor = no_typed
        assert await pm._get_global_stats_async() == {}
        assert no_typed.execute_async_calls == 0, "removed async dispatcher fallback must not fire"

        pm.system_monitor = None
        assert await pm._get_global_stats_async() == {}

    asyncio.run(_run())


# ─── SG-5: config validation against schema ─────────────────────────────────

def test_sg5_validate_config_against_schema():
    """Strict mode rejects unknown keys; lenient mode filters + logs.
    CR-5: error attribute is `fields_invalid`."""
    pm = CapabilityManager.__new__(CapabilityManager)
    pm.logger = logging.getLogger("test_sg5")

    schema = {
        "type": "object",
        "properties": {
            "model": {"type": "string", "default": "base"},
            "temperature": {"type": "number", "default": 0.0},
        },
    }

    # Known keys pass through
    assert pm._validate_config_against_schema({"model": "large"}, schema, "whisper") \
        == {"model": "large"}

    # No-schema manifest is a pass-through (substrate can't validate)
    assert pm._validate_config_against_schema({"anything": 1}, None, "whisper") \
        == {"anything": 1}
    assert pm._validate_config_against_schema({"anything": 1}, {"type": "object"}, "whisper") \
        == {"anything": 1}

    # Unknown key + strict=True (default) raises naming the keys
    try:
        pm._validate_config_against_schema(
            {"model": "large", "renamed_key": 9}, schema, "whisper")
    except CapabilityConfigError as e:
        assert e.fields_invalid == ["renamed_key"]
        assert e.config_class_name == "whisper"
    else:
        raise AssertionError("expected CapabilityConfigError")

    # Unknown key + strict=False filters (no exception)
    out = pm._validate_config_against_schema(
        {"model": "large", "renamed_key": 9}, schema, "whisper", strict=False)
    assert out == {"model": "large"}


# ─── CR-10: multi-instance bookkeeping ───────────────────────────────────────

def test_cr10_validate_instance_id():
    """_validate_instance_id rejects malformed input."""
    pm = CapabilityManager.__new__(CapabilityManager)
    pm.logger = logging.getLogger("test_cr10_validate")

    for good in ["whisper", "whisper-large", "whisper_v2", "a", "x" * 64]:
        pm._validate_instance_id(good)

    for bad in ["", "x" * 65, "has space", "has/slash", "has.dot", "has!bang",
                123, None, ["whisper"]]:
        try:
            pm._validate_instance_id(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"_validate_instance_id should reject {bad!r}")


def test_cr10_generate_instance_id():
    """_generate_instance_id produces unique `{name}-{6-char-hex}` IDs."""
    pm = CapabilityManager.__new__(CapabilityManager)
    pm.logger = logging.getLogger("test_cr10_generate")
    pm.instances = {}

    id1 = pm._generate_instance_id("whisper")
    assert id1.startswith("whisper-")
    suffix = id1[len("whisper-"):]
    assert len(suffix) == 6 and all(c in "0123456789abcdef" for c in suffix)

    pm.instances[id1] = CapabilityInstance(instance_id=id1, capability_name="whisper")
    id2 = pm._generate_instance_id("whisper")
    assert id2 != id1 and id2.startswith("whisper-")


def test_cr10_instance_queries():
    """get_instance + list_instances filter correctly."""
    pm = CapabilityManager.__new__(CapabilityManager)
    pm.logger = logging.getLogger("test_cr10_query")
    pm.instances = {
        "whisper": CapabilityInstance(instance_id="whisper", capability_name="whisper",
                                      proxy=_CR10StubProxy("whisper-default")),
        "whisper-large": CapabilityInstance(instance_id="whisper-large", capability_name="whisper",
                                            proxy=_CR10StubProxy("whisper-large")),
        "voxtral": CapabilityInstance(instance_id="voxtral", capability_name="voxtral",
                                      proxy=_CR10StubProxy("voxtral")),
    }

    assert pm.get_instance("whisper") is pm.instances["whisper"]
    assert pm.get_instance("whisper-large") is pm.instances["whisper-large"]
    assert pm.get_instance("nonexistent") is None

    assert len(pm.list_instances()) == 3
    assert {i.instance_id for i in pm.list_instances("whisper")} == {"whisper", "whisper-large"}
    assert {i.instance_id for i in pm.list_instances("voxtral")} == {"voxtral"}
    assert pm.list_instances("nonexistent") == []


def test_cr10_execute_routes_by_instance_id():
    """execute / enable / disable route to the correct CapabilityInstance."""
    pm = CapabilityManager.__new__(CapabilityManager)
    pm.capabilities = {}
    pm.instances = {}
    pm.discovered = []
    pm.logger = logging.getLogger("test_cr10_execute")
    pm.scheduler = PermissiveScheduler()
    pm.config_store = None
    pm._running_executions = set()
    pm._pending_disable_hooks = set()

    proxy_default = _CR10StubProxy("whisper-default")
    proxy_large = _CR10StubProxy("whisper-large")

    meta = CapabilityMeta(name="whisper", version="1.0.0")
    meta.instance = proxy_default
    pm.capabilities["whisper"] = meta
    pm.instances["whisper"] = CapabilityInstance(
        instance_id="whisper", capability_name="whisper", proxy=proxy_default)
    pm.instances["whisper-large"] = CapabilityInstance(
        instance_id="whisper-large", capability_name="whisper", proxy=proxy_large)

    # Execute routes to the addressed instance only
    assert pm.execute_capability("whisper", "audio.wav")["who"] == "whisper-default"
    assert len(proxy_default.execute_calls) == 1 and len(proxy_large.execute_calls) == 0
    assert pm.execute_capability("whisper-large", "audio.wav")["who"] == "whisper-large"
    assert len(proxy_large.execute_calls) == 1

    # disable only touches the addressed multi-instance
    assert pm.disable_capability("whisper-large") is True
    assert pm.instances["whisper-large"].enabled is False
    assert pm.instances["whisper"].enabled is True
    assert proxy_large.on_disable_calls == 1 and proxy_default.on_disable_calls == 0
    assert meta.enabled is True, "disabling multi-instance must not touch default's meta.enabled"

    # Default-instance enabled flag stays in sync with meta.enabled
    assert pm.disable_capability("whisper") is True
    assert pm.instances["whisper"].enabled is False and meta.enabled is False
    assert pm.enable_capability("whisper") is True
    assert pm.instances["whisper"].enabled is True and meta.enabled is True

    # Execute on the disabled multi-instance raises with the instance_id
    try:
        pm.execute_capability("whisper-large")
    except CapabilityDisabledError as e:
        assert e.capability_name == "whisper-large"
    else:
        raise AssertionError("expected CapabilityDisabledError")


def test_cr10_unload_canonical_keeps_remaining_instances():
    """Unloading the default instance with remaining multi-instances clears
    meta.instance but keeps CapabilityMeta + the other instances."""
    pm = CapabilityManager.__new__(CapabilityManager)
    pm.capabilities = {}
    pm.instances = {}
    pm.discovered = []
    pm.logger = logging.getLogger("test_cr10_unload")
    pm._running_executions = set()
    pm._pending_disable_hooks = set()

    proxy_default = _CR10StubProxy("whisper-default")
    proxy_large = _CR10StubProxy("whisper-large")
    meta = CapabilityMeta(name="whisper", version="1.0.0")
    meta.instance = proxy_default
    pm.capabilities["whisper"] = meta
    pm.instances["whisper"] = CapabilityInstance(
        instance_id="whisper", capability_name="whisper", proxy=proxy_default)
    pm.instances["whisper-large"] = CapabilityInstance(
        instance_id="whisper-large", capability_name="whisper", proxy=proxy_large)

    assert pm.unload_capability("whisper") is True
    assert "whisper" not in pm.instances
    assert "whisper-large" in pm.instances, "multi-instance must survive default unload"
    assert "whisper" in pm.capabilities
    assert pm.capabilities["whisper"].instance is None

    assert pm.unload_capability("whisper-large") is True
    assert "whisper-large" not in pm.instances
    assert "whisper" not in pm.capabilities, "no instances left → CapabilityMeta dropped"


# ─── CR-10b: async wrappers + concurrent batch APIs ──────────────────────────

def _fresh_pm_for_cr10b(name):
    pm = CapabilityManager.__new__(CapabilityManager)
    pm.logger = logging.getLogger(name)
    pm.capabilities = {}
    pm.instances = {}
    return pm


def test_cr10b_async_wrappers_forward_to_sync():
    """load_capability_async / unload_capability_async run the sync versions
    via asyncio.to_thread."""
    pm = _fresh_pm_for_cr10b("test_cr10b_async")
    load_calls = []
    unload_calls = []

    def fake_load(meta, config=None, strict=True, instance_id=None, new_instance=False):
        resolved = instance_id or meta.name
        load_calls.append((meta.name, config, instance_id, new_instance))
        pm.instances[resolved] = CapabilityInstance(
            instance_id=resolved, capability_name=meta.name, proxy=_CR10StubProxy(resolved))
        return True

    def fake_unload(name_or_id):
        unload_calls.append(name_or_id)
        pm.instances.pop(name_or_id, None)
        return True

    pm.load_capability = fake_load
    pm.unload_capability = fake_unload

    async def _run():
        meta = CapabilityMeta(name="whisper", version="1.0.0")
        meta.manifest = {}

        assert await pm.load_capability_async(meta) is True
        assert load_calls == [("whisper", None, None, False)]
        assert "whisper" in pm.instances

        assert await pm.unload_capability_async("whisper") is True
        assert unload_calls == ["whisper"]
        assert "whisper" not in pm.instances

    asyncio.run(_run())


def test_cr10b_load_capabilities_concurrent():
    """load_capabilities_concurrent fans out specs and returns instance_id dict."""
    pm = _fresh_pm_for_cr10b("test_cr10b_load_concurrent")

    def fake_load(meta, config=None, strict=True, instance_id=None, new_instance=False):
        resolved = instance_id or meta.name
        pm.instances[resolved] = CapabilityInstance(
            instance_id=resolved, capability_name=meta.name, proxy=_CR10StubProxy(resolved))
        return True

    pm.load_capability = fake_load
    whisper_meta = CapabilityMeta(name="whisper", version="1.0.0")
    whisper_meta.manifest = {}
    voxtral_meta = CapabilityMeta(name="voxtral", version="1.0.0")
    voxtral_meta.manifest = {}

    async def _run():
        specs = [
            CapabilityLoadSpec(meta=whisper_meta, instance_id="whisper-base"),
            CapabilityLoadSpec(meta=whisper_meta, instance_id="whisper-large"),
            CapabilityLoadSpec(meta=voxtral_meta),  # default → instance_id = "voxtral"
        ]
        result = await pm.load_capabilities_concurrent(specs)
        assert result == {
            "whisper-base": "whisper-base",
            "whisper-large": "whisper-large",
            "voxtral": "voxtral",
        }
        assert {"whisper-base", "whisper-large", "voxtral"}.issubset(pm.instances.keys())

    asyncio.run(_run())


def test_cr10b_partial_failure_semantics():
    """fail_fast=False collects exceptions; fail_fast=True re-raises."""
    pm = _fresh_pm_for_cr10b("test_cr10b_partial_failure")

    def fake_load(meta, config=None, strict=True, instance_id=None, new_instance=False):
        if meta.name == "broken":
            raise RuntimeError("simulated broken load")
        resolved = instance_id or meta.name
        pm.instances[resolved] = CapabilityInstance(
            instance_id=resolved, capability_name=meta.name, proxy=_CR10StubProxy(resolved))
        return True

    pm.load_capability = fake_load
    good_meta = CapabilityMeta(name="whisper", version="1.0.0")
    good_meta.manifest = {}
    bad_meta = CapabilityMeta(name="broken", version="1.0.0")
    bad_meta.manifest = {}

    async def _run():
        specs = [
            CapabilityLoadSpec(meta=good_meta, instance_id="whisper-good"),
            CapabilityLoadSpec(meta=bad_meta, instance_id="broken-bad"),
        ]

        result = await pm.load_capabilities_concurrent(specs, fail_fast=False)
        assert result["whisper-good"] == "whisper-good"
        assert isinstance(result["broken-bad"], Exception)
        assert "simulated broken load" in str(result["broken-bad"])

        pm.instances.pop("whisper-good", None)

        try:
            await pm.load_capabilities_concurrent(specs, fail_fast=True)
        except RuntimeError as e:
            assert "simulated broken load" in str(e)
        else:
            raise AssertionError("fail_fast=True should re-raise")

    asyncio.run(_run())


def test_cr10b_max_concurrency_caps_in_flight():
    """max_concurrency caps simultaneous loads (semaphore enforces the cap)."""
    pm = _fresh_pm_for_cr10b("test_cr10b_max_concurrency")
    in_flight = 0
    max_in_flight_observed = 0

    def fake_load(meta, config=None, strict=True, instance_id=None, new_instance=False):
        nonlocal in_flight, max_in_flight_observed
        in_flight += 1
        max_in_flight_observed = max(max_in_flight_observed, in_flight)
        time.sleep(0.02)  # give other tasks a chance to race in
        in_flight -= 1
        resolved = instance_id or meta.name
        pm.instances[resolved] = CapabilityInstance(
            instance_id=resolved, capability_name=meta.name, proxy=_CR10StubProxy(resolved))
        return True

    pm.load_capability = fake_load
    meta = CapabilityMeta(name="whisper", version="1.0.0")
    meta.manifest = {}

    async def _run():
        nonlocal in_flight, max_in_flight_observed
        specs = [CapabilityLoadSpec(meta=meta, instance_id=f"w{i}") for i in range(8)]
        await pm.load_capabilities_concurrent(specs, max_concurrency=None)
        assert max_in_flight_observed >= 2, \
            f"unbounded concurrency should allow >=2 in flight, got {max_in_flight_observed}"

        for spec in specs:
            pm.instances.pop(spec.instance_id, None)
        in_flight = 0
        max_in_flight_observed = 0
        await pm.load_capabilities_concurrent(specs, max_concurrency=2)
        assert max_in_flight_observed <= 2, \
            f"max_concurrency=2 must cap at 2, observed {max_in_flight_observed}"

    asyncio.run(_run())


def test_cr10b_unload_capabilities_concurrent():
    """unload_capabilities_concurrent fans out and returns success/failure dict."""
    pm = _fresh_pm_for_cr10b("test_cr10b_unload_concurrent")
    pm.instances = {
        "whisper": CapabilityInstance(instance_id="whisper", capability_name="whisper"),
        "voxtral": CapabilityInstance(instance_id="voxtral", capability_name="voxtral"),
    }

    def fake_unload(name_or_id):
        if name_or_id == "missing":
            raise RuntimeError("not found")
        pm.instances.pop(name_or_id, None)
        return True

    pm.unload_capability = fake_unload

    async def _run():
        result = await pm.unload_capabilities_concurrent(["whisper", "voxtral", "missing"])
        assert result["whisper"] is True
        assert result["voxtral"] is True
        assert isinstance(result["missing"], Exception)
        assert "whisper" not in pm.instances and "voxtral" not in pm.instances

    asyncio.run(_run())


# ─── CR-7: reactive retry + sampling + SG-33 limiter ─────────────────────────

class _CR7StubProxy:
    """Stub proxy with controllable failure behavior for CR-7 reactive tests."""
    def __init__(self, name="stub"):
        self._name = name
        self.execute_calls = 0
        self.fail_next_n_times = 0
        self.fail_with: Optional[Exception] = None
    @property
    def name(self): return self._name
    @property
    def version(self): return "0.0.1"
    def initialize(self, config): pass
    def execute(self, *args, **kwargs):
        self.execute_calls += 1
        if self.fail_next_n_times > 0:
            self.fail_next_n_times -= 1
            if self.fail_with is not None:
                raise self.fail_with
        return {"result": "ok", "call": self.execute_calls, "proxy": self._name}
    def get_stats(self):
        # Synthetic stats matching the substrate's /stats contract (post-subtree
        # fix): gpu_memory_mb is NOT a /stats key — GPU memory comes from sysmon's
        # per-PID enumeration via the substrate helper, not from the worker.
        return {"pid": 12345, "cpu_percent": 50.0, "memory_rss_mb": 1000.0,
                "subtree_pids": [12345]}
    def get_config_schema(self): return {}
    def get_current_config(self): return {}
    def cleanup(self): pass
    def on_disable(self): pass
    def on_enable(self): pass


def _build_cr7_test_pm():
    """CapabilityManager via __new__ + manually-populated CR-7 attrs (the
    established CR-2/CR-10 fixture pattern: no lazy store init against the
    user's filesystem)."""
    pm = CapabilityManager.__new__(CapabilityManager)
    pm.capabilities = {}
    pm.instances = {}
    pm.discovered = []
    pm.logger = logging.getLogger("test_cr7")
    pm.scheduler = PermissiveScheduler()
    pm.config_store = None
    pm._running_executions = set()
    pm._pending_disable_hooks = set()
    pm.empirical_store = None
    pm.max_retries = 1
    pm._concurrent_limiters = {}
    return pm


def _make_reload_stub(pm, capability_name, config_hash):
    """Stub `reload_capability` that swaps in a fresh (non-failing) _CR7StubProxy."""
    reload_calls = []

    def fake_reload(name_or_id, config=None):
        reload_calls.append((name_or_id, dict(config) if config else None))
        new_proxy = _CR7StubProxy(f"{capability_name}-reloaded")
        pm.instances[name_or_id] = CapabilityInstance(
            instance_id=name_or_id, capability_name=capability_name,
            proxy=new_proxy, config_hash=config_hash, config=dict(config or {}))
        if capability_name in pm.capabilities:
            pm.capabilities[capability_name].instance = new_proxy
        return True

    pm.reload_capability = fake_reload
    return reload_calls


def _cr7_reload_then_retry(error_to_raise, label):
    """Shared body: CapabilityResourceError → reload → retry → success.
    Track A (WorkerOOMError) + Track B (capability-raised) converge on the
    always-reload path (PyTorch CUDA allocator fragmentation rationale)."""
    pm = _build_cr7_test_pm()

    proxy = _CR7StubProxy(f"{label}-capability")
    meta = CapabilityMeta(name=f"{label}-capability", version="1.0.0")
    meta.instance = proxy
    meta.manifest = {"python_path": "/fake"}
    pm.capabilities[f"{label}-capability"] = meta
    pm.instances[f"{label}-capability"] = CapabilityInstance(
        instance_id=f"{label}-capability", capability_name=f"{label}-capability",
        proxy=proxy, config_hash="sha256:test", config={"model": "base"})

    reload_calls = _make_reload_stub(pm, f"{label}-capability", "sha256:test")

    proxy.fail_next_n_times = 1
    proxy.fail_with = error_to_raise

    result = pm.execute_capability(f"{label}-capability")
    assert isinstance(result, dict) and result.get("result") == "ok"
    assert result["proxy"] == f"{label}-capability-reloaded", \
        f"{label}: second attempt must hit the reloaded proxy"
    assert len(reload_calls) == 1
    assert reload_calls[0][1] == {"model": "base"}, \
        f"{label}: reload must receive saved config"


def test_cr7_track_a_workeroom_reloads():
    _cr7_reload_then_retry(
        WorkerOOMError("track-a-capability", process_returncode=-9), label="track-a")


def test_cr7_track_b_capability_resource_error_reloads():
    _cr7_reload_then_retry(
        CapabilityResourceError(
            "track-b: CUDA OOM",
            resource_shortfall=ResourceShortfall(
                resource='gpu_vram_mb', needed=8000.0, available=4000.0)),
        label="track-b")


def test_cr7_max_retries_exhausted_raises():
    """All retries fail → final CapabilityResourceError raised."""
    pm = _build_cr7_test_pm()
    pm.max_retries = 1  # one retry; total 2 attempts

    proxy = _CR7StubProxy("always-fails")
    meta = CapabilityMeta(name="always-fails", version="1.0.0")
    meta.instance = proxy
    meta.manifest = {"python_path": "/fake"}
    pm.capabilities["always-fails"] = meta
    pm.instances["always-fails"] = CapabilityInstance(
        instance_id="always-fails", capability_name="always-fails",
        proxy=proxy, config_hash="sha256:def")

    attempt_count = [0]

    def fake_reload_always_fails(name_or_id, config=None):
        attempt_count[0] += 1
        new_proxy = _CR7StubProxy(f"always-fails-reloaded-{attempt_count[0]}")
        new_proxy.fail_next_n_times = 99
        new_proxy.fail_with = CapabilityResourceError("persistent failure")
        pm.instances[name_or_id] = CapabilityInstance(
            instance_id=name_or_id, capability_name="always-fails",
            proxy=new_proxy, config_hash="sha256:def")
        return True
    pm.reload_capability = fake_reload_always_fails

    proxy.fail_next_n_times = 99
    proxy.fail_with = CapabilityResourceError("persistent failure")

    raised = False
    try:
        pm.execute_capability("always-fails")
    except CapabilityResourceError as e:
        raised = True
        assert "persistent failure" in str(e)
    assert raised, "expected CapabilityResourceError after retry exhaustion"
    assert attempt_count[0] == 1, \
        f"max_retries=1 → 1 reload between 2 total attempts, got {attempt_count[0]}"


def test_cr7_no_retry_when_max_retries_zero():
    """max_retries=0 → no retry, first failure propagates without reload."""
    pm = _build_cr7_test_pm()
    pm.max_retries = 0

    proxy = _CR7StubProxy("no-retry")
    meta = CapabilityMeta(name="no-retry", version="1.0.0")
    meta.instance = proxy
    meta.manifest = {"python_path": "/fake"}
    pm.capabilities["no-retry"] = meta
    pm.instances["no-retry"] = CapabilityInstance(
        instance_id="no-retry", capability_name="no-retry",
        proxy=proxy, config_hash="sha256:nort")
    reload_calls = _make_reload_stub(pm, "no-retry", "sha256:nort")

    proxy.fail_next_n_times = 1
    proxy.fail_with = CapabilityResourceError("first-attempt failure")

    raised = False
    try:
        pm.execute_capability("no-retry")
    except CapabilityResourceError:
        raised = True
    assert raised
    assert proxy.execute_calls == 1, "max_retries=0 must mean 1 total attempt"
    assert reload_calls == [], "max_retries=0 must NOT trigger reload"


def test_cr7_sample_recording_persists(tmp_path):
    """Sample recording succeeds + persists; second execute folds via Welford."""
    pm = _build_cr7_test_pm()
    pm.empirical_store = LocalEmpiricalResourceStore(tmp_path / "empirical.db")

    proxy = _CR7StubProxy("sampled")
    meta = CapabilityMeta(name="sampled", version="1.0.0")
    meta.instance = proxy
    pm.capabilities["sampled"] = meta
    pm.instances["sampled"] = CapabilityInstance(
        instance_id="sampled", capability_name="sampled",
        proxy=proxy, config_hash="sha256:xyz")

    pm.execute_capability("sampled")
    rec = pm.empirical_store.get_record("sampled", "sha256:xyz")
    assert rec is not None, "sample recording must have created a record"
    assert rec.sample_count == 1
    assert rec.success_rate == 1.0
    assert rec.cpu_percent_mean == 50.0
    assert rec.memory_mb_peak_mean == 1000.0

    pm.execute_capability("sampled")
    rec = pm.empirical_store.get_record("sampled", "sha256:xyz")
    assert rec.sample_count == 2
    assert abs(rec.cpu_percent_mean - 50.0) < 1e-9, \
        "two samples of cpu=50 should leave Welford mean unchanged"


def test_cr7_no_sample_recording_when_store_absent():
    """Sample recording skipped silently when empirical_store is None."""
    pm = _build_cr7_test_pm()  # empirical_store=None

    proxy = _CR7StubProxy("no-store")
    meta = CapabilityMeta(name="no-store", version="1.0.0")
    meta.instance = proxy
    pm.capabilities["no-store"] = meta
    pm.instances["no-store"] = CapabilityInstance(
        instance_id="no-store", capability_name="no-store",
        proxy=proxy, config_hash="sha256:nostore")

    assert pm.execute_capability("no-store")["result"] == "ok"


def test_sg33_concurrent_limiter_created_and_cached():
    """SG-33: per-instance asyncio.Semaphore created with the right cap + cached."""
    pm = _build_cr7_test_pm()

    proxy = _CR7StubProxy("limited")
    meta = CapabilityMeta(name="limited", version="1.0.0")
    meta.instance = proxy
    pm.capabilities["limited"] = meta
    pm.instances["limited"] = CapabilityInstance(
        instance_id="limited", capability_name="limited",
        proxy=proxy, config_hash="sha256:lim", max_concurrent_requests=2)

    limiter = pm._get_concurrent_limiter("limited")
    assert limiter is not None
    assert limiter._value == 2, f"semaphore should start at 2, got {limiter._value}"
    assert pm._get_concurrent_limiter("limited") is limiter, "limiter must be cached"

    # No cap → None (unbounded); missing instance → None
    pm.instances["limited"].max_concurrent_requests = None
    pm._concurrent_limiters.pop("limited", None)
    assert pm._get_concurrent_limiter("limited") is None
    assert pm._get_concurrent_limiter("nonexistent") is None


def test_cr7_eviction_candidates_multi_axis():
    """_evict_for_resources no longer filters by requires_gpu (CR-7 multi-axis):
    both GPU and CPU-only loaded capabilities are candidates."""
    pm = _build_cr7_test_pm()

    gpu_proxy = _CR7StubProxy("gpu-cand")
    gpu_meta = CapabilityMeta(name="gpu-cand", version="1.0.0",
                              resources=ResourceRequirements(requires_gpu=True))
    gpu_meta.instance = gpu_proxy
    gpu_meta.manifest = {"resources": {"requires_gpu": True}}
    gpu_meta.last_executed = 100.0
    pm.capabilities["gpu-cand"] = gpu_meta
    pm.instances["gpu-cand"] = CapabilityInstance(
        instance_id="gpu-cand", capability_name="gpu-cand",
        proxy=gpu_proxy, config_hash="sha256:gpu")

    cpu_proxy = _CR7StubProxy("cpu-cand")
    cpu_meta = CapabilityMeta(name="cpu-cand", version="1.0.0",
                              resources=ResourceRequirements(requires_gpu=False))
    cpu_meta.instance = cpu_proxy
    cpu_meta.manifest = {"resources": {"requires_gpu": False}}
    cpu_meta.last_executed = 50.0  # idler than gpu-cand
    pm.capabilities["cpu-cand"] = cpu_meta
    pm.instances["cpu-cand"] = CapabilityInstance(
        instance_id="cpu-cand", capability_name="cpu-cand",
        proxy=cpu_proxy, config_hash="sha256:cpu")

    needed_meta = CapabilityMeta(name="needs-gpu", version="1.0.0",
                                 resources=ResourceRequirements(requires_gpu=True))

    candidates = [
        meta for name, meta in pm.capabilities.items()
        if meta.instance is not None and name != needed_meta.name
    ]
    assert {c.name for c in candidates} == {"gpu-cand", "cpu-cand"}, \
        "multi-axis: both GPU and CPU capabilities must be candidates"


# ─── CR-12: worker-env overlay + secrets ─────────────────────────────────────

class _FakeSecretStore:
    def __init__(self): self._d = {}
    def get_secret(self, capability_name, key, *, scope=None):
        return self._d.get((scope, capability_name, key))
    def set_secret(self, capability_name, key, value, *, scope=None):
        self._d[(scope, capability_name, key)] = value
    def delete_secret(self, capability_name, key, *, scope=None):
        return self._d.pop((scope, capability_name, key), None) is not None
    def list_keys(self, capability_name, *, scope=None):
        return sorted(k for (s, p, k) in self._d if s == scope and p == capability_name)


def test_cr12_worker_env_overlay_and_secret_actuation():
    """Overlay composition: unset secret OMITTED (not injected empty); visible
    default present; set_capability_secret satisfies required; values never leak."""
    we = [
        {"name": "WE_API_KEY", "secret": True, "required": True, "label": "Key",
         "description": "", "default": None, "options": None},
        {"name": "WE_DEVICE", "secret": False, "required": False, "label": "Dev",
         "description": "", "default": "0", "options": None},
    ]
    meta = CapabilityMeta(name="weplug", version="1.0.0", description="d")
    meta.manifest_v2 = ManifestV2(code=CodeSection(name="weplug", worker_env=we))
    meta.manifest = {"worker_env": we}

    pm = CapabilityManager.__new__(CapabilityManager)
    pm.secret_store = _FakeSecretStore()
    pm.logger = logging.getLogger("cr12-test")
    pm.capabilities = {}
    pm.instances = {}

    # Secret UNSET: omitted from overlay; visible default present; required missing.
    assert pm._resolve_worker_env(meta) == {"WE_DEVICE": "0"}
    assert pm.missing_required_env(meta) == ["WE_API_KEY"]
    st = {s["name"]: s for s in pm.get_worker_env_status(meta)}
    assert st["WE_API_KEY"]["secret"] and st["WE_API_KEY"]["required"]
    assert st["WE_API_KEY"]["satisfied"] is False and st["WE_DEVICE"]["satisfied"] is True

    # set_capability_secret(reload=False) stores; now injected + required satisfied.
    pm.set_capability_secret("weplug", "WE_API_KEY", "sk-test-123", reload=False)
    assert pm._resolve_worker_env(meta) == {"WE_API_KEY": "sk-test-123", "WE_DEVICE": "0"}
    assert pm.missing_required_env(meta) == []
    assert "sk-test-123" not in str(pm.get_worker_env_status(meta)), \
        "status must never leak the secret VALUE"

    # Secret keyed by capability_name → shared across instances of the capability.
    assert pm.secret_store.get_secret("weplug", "WE_API_KEY") == "sk-test-123"

    # Flat-manifest fallback (no manifest_v2) resolves identically.
    meta2 = CapabilityMeta(name="weplug", version="1.0.0")
    meta2.manifest = {"worker_env": we}
    assert pm._resolve_worker_env(meta2) == {"WE_API_KEY": "sk-test-123", "WE_DEVICE": "0"}

    # No WORKER_ENV contract → empty overlay, nothing missing.
    plain = CapabilityMeta(name="plain", version="1.0.0")
    plain.manifest = {}
    assert pm._resolve_worker_env(plain) == {}
    assert pm.missing_required_env(plain) == []


# ─── GPU subtree attribution in _record_sample_safe ──────────────────────────

class _StubProxyWithSubtree:
    """Proxy whose /stats reports a worker pid + grandchild pid (vLLM-shaped)."""
    def get_stats(self):
        return {"pid": 1111, "cpu_percent": 50.0, "memory_rss_mb": 1024.0,
                "subtree_pids": [1111, 9999]}  # 9999 = vLLM grandchild


class _StubSysmonMeta:
    """CapabilityMeta-shaped wrapper so _get_sysmon_capability resolves."""
    def __init__(self, sysmon):
        self.instance = sysmon


class _StubSysmon:
    def list_processes(self):
        # Grandchild 9999 holds 4096 MB GPU memory; pre-fix substrate (matching
        # only worker pid 1111) reported 0; the fixed path sums across the subtree.
        return [
            {"pid": 9999, "gpu_index": 0, "gpu_memory_mb": 4096.0},
            {"pid": 7777, "gpu_index": 0, "gpu_memory_mb": 512.0},  # unrelated
        ]


class _SpyStore:
    """Capture record_sample calls instead of writing to disk."""
    def __init__(self):
        self.samples = []
    def record_sample(self, instance_id, capability_name, config_hash, sample):
        self.samples.append((instance_id, capability_name, config_hash, sample))


def test_gpu_subtree_attribution_sums_grandchildren():
    """With a sysmon configured and a worker reporting subtree_pids, the sample's
    gpu_memory_mb_peak comes from the helper's intersection (not a never-emitted
    worker /stats key — the pre-fix bug)."""
    pm = CapabilityManager.__new__(CapabilityManager)
    pm.capabilities = {"nvidia-monitor": _StubSysmonMeta(_StubSysmon())}
    pm.instances = {}
    pm.logger = logging.getLogger("test_gpu_subtree_attr")
    pm._sysmon_capability_name = "nvidia-monitor"
    pm.empirical_store = _SpyStore()
    inst = CapabilityInstance(
        instance_id="voxtral-vllm-test", capability_name="voxtral-vllm",
        proxy=_StubProxyWithSubtree(), config_hash="abc123", config={})
    pm._record_sample_safe(inst, start_time=0.0, success=True)
    assert len(pm.empirical_store.samples) == 1
    _, _, _, sample = pm.empirical_store.samples[0]
    assert sample.gpu_memory_mb_peak == 4096.0, \
        f"expected grandchild GPU attribution, got {sample.gpu_memory_mb_peak}"
    assert sample.memory_mb_peak == 1024.0
    assert sample.cpu_percent == 50.0


def test_gpu_subtree_attribution_without_sysmon_records_zero():
    """No sysmon configured → gpu_memory_mb_peak records as 0.0 (honest signal)."""
    pm = CapabilityManager.__new__(CapabilityManager)
    pm.capabilities = {}
    pm.instances = {}
    pm.logger = logging.getLogger("test_gpu_subtree_no_sysmon")
    pm._sysmon_capability_name = None
    pm.empirical_store = _SpyStore()
    inst = CapabilityInstance(
        instance_id="cpu-only-test", capability_name="nltk",
        proxy=_StubProxyWithSubtree(), config_hash="cpu123", config={})
    pm._record_sample_safe(inst, start_time=0.0, success=True)
    assert len(pm.empirical_store.samples) == 1
    _, _, _, sample = pm.empirical_store.samples[0]
    assert sample.gpu_memory_mb_peak == 0.0


def test_reactive_eviction_sees_named_instances_and_sizes_to_real_need():
    """335023d6: reactive eviction must (a) see CR-10 NAMED instances (the
    live-case resident whisper family was invisible to the meta-keyed candidate
    set), (b) run largest-first sized to the needy instance's empirical GPU
    peak — NOT CUDA's marginal shortfall, and (c) evict ALL idle GPU residents
    when the needy instance is unprofiled (its measurement run). Mid-execute
    instances and CPU-only residents are never candidates."""
    pm = _build_cr7_test_pm()

    class _ReleasingProxy(_CR7StubProxy):
        def __init__(self, name):
            super().__init__(name)
            self.released = False
        def release(self):
            self.released = True

    class _PeakStore:
        def __init__(self):
            self.peaks = {}
            self.failed_only = set()  # instance ids whose samples ALL failed
        def get_record(self, instance_id, config_hash):
            peak = self.peaks.get((instance_id, config_hash))
            if peak is None:
                return None
            rate = 0.0 if instance_id in self.failed_only else 1.0
            class _Rec:
                gpu_memory_mb_peak_max = peak
                memory_mb_peak_max = 100.0
                success_rate = rate
            return _Rec()

    store = _PeakStore()
    pm.empirical_store = store

    def add(iid, peak, executing=False):
        proxy = _ReleasingProxy(iid)
        pm.instances[iid] = CapabilityInstance(
            instance_id=iid, capability_name="cjm-capability-whisper",
            proxy=proxy, config_hash=f"sha256:{iid}")
        if peak is not None:
            store.peaks[(iid, f"sha256:{iid}")] = peak
        if executing:
            pm._running_executions.add(iid)
        return proxy

    large_a = add("whisper--large-v2", 9912.0)
    large_b = add("whisper--large-v3", 9912.0)
    small = add("whisper--small", 1992.0)
    cpu_only = add("cjm-capability-ffmpeg", 0.0)
    busy = add("cjm-capability-voxtral-hf", 9506.0, executing=True)
    add("whisper--medium", 3072.0)  # the needy instance (profiled case)

    needed_meta = CapabilityMeta(name="cjm-capability-whisper", version="1.0.0")

    # (b) profiled needy: target 3072MB -> ONE large goes (9912 covers it), rest stay
    assert pm._reactive_evict_for(needed_meta,
                                  needy_instance_id="whisper--medium") is True
    assert large_a.released != large_b.released, "largest-first, ONE large suffices"
    assert not small.released and not cpu_only.released and not busy.released
    assert not pm.instances["whisper--medium"].proxy.released, "never evict the needy instance"

    # (c) unprofiled needy: no target derivable -> ALL idle GPU residents go
    for p in (large_a, large_b, small):
        p.released = False
    store.peaks.pop(("whisper--medium", "sha256:whisper--medium"))
    assert pm._reactive_evict_for(needed_meta,
                                  needy_instance_id="whisper--medium") is True
    assert large_a.released and large_b.released and small.released, \
        "unprofiled needy = clean the GPU (residents lazy-reload later)"
    assert not cpu_only.released and not busy.released, \
        "CPU-only + mid-execute instances are never candidates"

    # (d) a needy record built ONLY from failed attempts cannot size a target —
    # its peak is how far the load got BEFORE the OOM (stress-3 live case:
    # 5184MB recorded vs whisper-large's real ~9.9GB) -> treated as unprofiled
    for p in (large_a, large_b, small):
        p.released = False
    store.peaks[("whisper--medium", "sha256:whisper--medium")] = 5184.0
    store.failed_only.add("whisper--medium")
    assert pm._reactive_evict_for(needed_meta,
                                  needy_instance_id="whisper--medium") is True
    assert large_a.released and large_b.released and small.released, \
        "failed-only needy record = clean sweep, never a 5184MB target"


def test_admission_idle_eviction_frees_shortfall_with_exclusions():
    """9b0c8eb1 (eviction-v2 A): evict_idle_gpu frees ~1.25x the shortfall
    LARGEST-FIRST, never touching excluded instances (the queue's structural
    hysteresis: in-flight + pending-targeted), mid-execute instances, or
    CPU-only residents; returns the freed estimate (0.0 when nothing
    qualifies — the caller's re-scan then re-blocks with reason)."""
    pm = _build_cr7_test_pm()

    class _ReleasingProxy(_CR7StubProxy):
        def __init__(self, name):
            super().__init__(name)
            self.released = False
        def release(self):
            self.released = True

    class _PeakStore:
        def __init__(self):
            self.peaks = {}
        def get_record(self, instance_id, config_hash):
            peak = self.peaks.get((instance_id, config_hash))
            if peak is None:
                return None
            class _Rec:
                gpu_memory_mb_peak_max = peak
                memory_mb_peak_max = 100.0
                success_rate = 1.0
            return _Rec()

    store = _PeakStore()
    pm.empirical_store = store

    def add(iid, peak, executing=False):
        proxy = _ReleasingProxy(iid)
        pm.instances[iid] = CapabilityInstance(
            instance_id=iid, capability_name="cjm-capability-whisper",
            proxy=proxy, config_hash=f"sha256:{iid}")
        if peak is not None:
            store.peaks[(iid, f"sha256:{iid}")] = peak
        if executing:
            pm._running_executions.add(iid)
        return proxy

    large_a = add("whisper--large-v2", 9912.0)
    small = add("whisper--small", 1992.0)
    pending_tgt = add("whisper--large-v3", 9912.0)  # queue-known pending target
    cpu_only = add("cjm-capability-ffmpeg", 0.0)
    busy = add("cjm-capability-voxtral-hf", 9506.0, executing=True)

    # target = 3000 * 1.25 = 3750MB -> the largest NON-excluded resident
    # (large-v2, 9912MB) covers it alone; everyone else stays.
    freed = pm.evict_idle_gpu(3000.0, exclude_instance_ids=["whisper--large-v3"])
    assert freed == 9912.0
    assert large_a.released
    assert not small.released and not pending_tgt.released
    assert not cpu_only.released and not busy.released

    # Everything evictable excluded -> nothing freed, no raise.
    small.released = False
    freed2 = pm.evict_idle_gpu(3000.0, exclude_instance_ids=[
        "whisper--large-v2", "whisper--large-v3", "whisper--small"])
    assert freed2 == 0.0
    assert not small.released
