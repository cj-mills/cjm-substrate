"""Capability discovery, loading, and lifecycle management via process isolation."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field as _field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Tuple, Type, Union

from cjm_substrate.core._telemetry import attribute_gpu_to_worker_subtree
from cjm_substrate.core.adapter_manifest import (adapter_manifest_from_dict, AdapterManifest,
                                                 is_adapter_manifest,
                                                 match_protocol_against_surface)
from cjm_substrate.core.capability import ToolCapability
from cjm_substrate.core.config import get_config
from cjm_substrate.core.config_store import (CapabilityConfigRecord, CapabilityConfigStore,
                                             LocalCapabilityConfigStore)
from cjm_substrate.core.diagnostics_store import DiagnosticsStore, LocalDiagnosticsStore
from cjm_substrate.core.empirical_store import (compute_config_hash, EmpiricalResourceStore,
                                                LocalEmpiricalResourceStore, ResourceSample)
from cjm_substrate.core.errors import (CapabilityConfigError, CapabilityDisabledError,
                                       CapabilityResourceError)
from cjm_substrate.core.journal_store import (JournalEvent, JournalStore, LocalJournalStore,
                                              SubstrateEventType)
from cjm_substrate.core.manifest_format import (compute_config_schema_hash, load_manifest,
                                                manifest_to_dict)
from cjm_substrate.core.metadata import (CapabilityInstance, CapabilityLoadSpec, CapabilityMeta,
                                         ResourceRequirements)
from cjm_substrate.core.proxy import RemoteCapabilityProxy
from cjm_substrate.core.scheduling import PermissiveScheduler, ResourceScheduler
from cjm_substrate.core.secret_store import LocalSecretStore, SecretStore


# SG-39: library modules use `logging.getLogger(__name__)` and let the host
# (CLI entry point, FastHTML app, worker subprocess) own `basicConfig`.
# Previously each module called `logging.basicConfig(force=True, format=...)`
# at import time; the last-imported module's format silently overrode the
# others.


class CapabilityManager:
    """Manages capability discovery, loading, and lifecycle via process isolation.

    Orchestrates the complete capability lifecycle in the process-isolated
    architecture: DISCOVERY (manifests in local `.cjm/manifests/` shadowing the
    config-based global dir), LOADING (RemoteCapabilityProxy instances that spawn
    isolated worker subprocesses in their own conda envs), EXECUTION (calls
    forwarded to workers over HTTP, sync + async + streaming), and LIFECYCLE
    (initialization, configuration updates, enable/disable, cleanup).
    """
    def __init__(
        self,
        capability_interface:Type[ToolCapability]=ToolCapability, # Base interface for type checking
        search_paths:Optional[List[Path]]=None, # Custom manifest search paths
        scheduler:Optional[ResourceScheduler]=None, # Resource allocation policy
        config_store:Optional[CapabilityConfigStore]=None, # CR-2: persistence backend; lazy LocalCapabilityConfigStore default per OQ-4
        empirical_store:Optional[EmpiricalResourceStore]=None, # CR-7: resource-usage tracking backend; lazy LocalEmpiricalResourceStore when cfg.substrate.empirical_tracking
        secret_store:Optional[SecretStore]=None, # CR-12: secret backend; lazy LocalSecretStore default (project-local <data_dir>/secrets)
        max_retries:int=1, # CR-7: how many reactive retries to attempt on CapabilityResourceError (default 1 — one retry after eviction)
        sysmon_capability_name:Optional[str]=None, # monitor capability (CR-3) name for GPU subtree attribution; default-None records skip GPU attribution (compute axis only)
        journal_store:Optional[JournalStore]=None, # CR-14: durable account-of-action; lazy LocalJournalStore at <data_dir>/journal.db
        diagnostics_store:Optional[DiagnosticsStore]=None # CR-14: disposable diagnostic narrative; lazy LocalDiagnosticsStore at <data_dir>/diagnostics.db
    ):
        """Initialize the capability manager."""
        self.capability_interface = capability_interface
        
        # Use config-based search paths if not explicitly provided
        if search_paths is None:
            cfg = get_config()
            self.search_paths = [
                Path.cwd() / ".cjm" / "manifests",  # Local (high priority)
                cfg.manifests_dir                    # Config-based (replaces ~/.cjm/manifests)
            ]
        else:
            self.search_paths = search_paths
        
        self.scheduler = scheduler or PermissiveScheduler()
        self.system_monitor: Optional[ToolCapability] = None
        self.discovered: List[CapabilityMeta] = []
        self.capabilities: Dict[str, CapabilityMeta] = {}
        # CR-10: per-instance state keyed by instance_id. Default-loaded capabilities
        # populate self.instances[capability_name] alongside self.capabilities[capability_name]
        # for backward compat; multi-instance loads populate self.instances only.
        self.instances: Dict[str, CapabilityInstance] = {}
        self.logger = logging.getLogger(f"{__name__}.{type(self).__name__}")
        
        # CR-2: persistence + lifecycle hook bookkeeping.
        # config_store=None → lazy LocalCapabilityConfigStore (~/.cjm/capability_configs.db)
        # per OQ-4 resolution. Hosts that want a different backend (workflow-
        # scoped, in-memory for tests) pass an explicit CapabilityConfigStore.
        # Resolve the project-local data dir once for all substrate stores
        # (cfg.data_dir; stores fall back to ~/.cjm when this is None).
        try:
            _data_dir = get_config().data_dir
        except Exception:
            _data_dir = None
        self.config_store: CapabilityConfigStore = config_store or LocalCapabilityConfigStore(
            (_data_dir / "capability_configs.db") if _data_dir is not None else None
        )
        # Track capabilities with in-flight execute calls so disable_capability can defer
        # the on_disable hook until the job finishes (audit semantics).
        self._running_executions: Set[str] = set()
        self._pending_disable_hooks: Set[str] = set()
        
        # CR-12: secret storage (API keys etc.). Project-local by default:
        # <cfg.data_dir>/secrets (falls back to ~/.cjm/secrets). Secret values are
        # NEVER persisted via config_store, echoed in config_schema, or logged.
        # Hosts pass an explicit SecretStore for keyring / multi-user backends.
        self.secret_store: SecretStore = secret_store or LocalSecretStore(
            (_data_dir / "secrets") if _data_dir is not None else None
        )
        
        # CR-14: observability stores. The journal is the substrate-derived,
        # host-written, never-auto-deleted account-of-action (SG-57's audit
        # trail); diagnostics is the disposable worker-narrative store. Both
        # default to the project-local data dir beside the sibling stores.
        # The JobQueue + proxies adopt these via getattr/constructor pass-
        # through, so host + queue + workers share ONE journal + ONE
        # diagnostics DB per runtime.
        self.journal_store: JournalStore = journal_store or LocalJournalStore(
            (_data_dir / "journal.db") if _data_dir is not None else None
        )
        self.diagnostics_store: DiagnosticsStore = diagnostics_store or LocalDiagnosticsStore(
            (_data_dir / "diagnostics.db") if _data_dir is not None else None
        )
        # CR-14 follow-up: retention is INVOKED, not just implemented (the
        # G11 inert-API lesson — an API nobody calls is inert). A daemon
        # thread sweeps the diagnostics store at host startup per the
        # cfg.substrate policy; `cjm-ctl retention` is the explicit operator
        # invocation. The journal is never swept.
        self._start_diagnostics_retention_sweep()

        # CR-7: empirical resource tracking. The store is lazy-init'd only when
        # cfg.substrate.empirical_tracking is True (default). Hosts that want
        # the substrate to skip recording entirely set empirical_tracking=False
        # in cjm.yaml; explicit empirical_store=... bypasses the flag check and
        # uses the supplied store (useful for tests + workflow-scoped stores).
        if empirical_store is not None:
            self.empirical_store: Optional[EmpiricalResourceStore] = empirical_store
        else:
            try:
                _cfg = get_config()
                if getattr(_cfg.substrate, 'empirical_tracking', True):
                    self.empirical_store = LocalEmpiricalResourceStore(
                        (_data_dir / "empirical_resources.db") if _data_dir is not None else None
                    )
                else:
                    self.empirical_store = None
            except Exception:
                # Backward-compat: pre-CR-7 CJMConfig without substrate sub-config.
                # Default-on behavior; lazy-create the store.
                self.empirical_store = LocalEmpiricalResourceStore(
                    (_data_dir / "empirical_resources.db") if _data_dir is not None else None
                )
        
        # CR-7: bounded reactive retries on CapabilityResourceError (Track A + B).
        self.max_retries: int = max_retries
        
        # monitor capability name for GPU subtree attribution at sample-record time.
        # _record_sample_safe intersects the worker-reported subtree_pids with this
        # capability's list_processes() output. Mirrors JobQueue's sysmon_capability_name;
        # hosts typically configure both with the same value. Lazy-resolved via
        # self.capabilities to tolerate load-order (sysmon may load after this manager
        # is constructed).
        self._sysmon_capability_name: Optional[str] = sysmon_capability_name
        
        # SG-33 (part-of-CR-7): per-instance asyncio.Semaphore for the async
        # execute path's concurrency cap. Lazy-created on first execute_capability_async
        # for instances whose `max_concurrent_requests` was set at load time.
        # Sync execute_capability is NOT gated — sync callers can't await a semaphore.
        self._concurrent_limiters: Dict[str, asyncio.Semaphore] = {}

    def _start_diagnostics_retention_sweep(self) -> None:
        """CR-14 follow-up: host-startup diagnostics retention sweep.

        The invocation half of the retention policy (`cjm-ctl retention` is the
        other): fire-and-forget daemon thread so `__init__` stays fast (slow-init
        discipline) and a large backlog never delays capability loading. Disabled
        when `cfg.substrate.diagnostics_retention_days <= 0` and no size budget
        is set. Best-effort: a sweep failure logs at WARNING — the diagnostics
        class is disposable; the JOURNAL has no retention surface at all.
        """
        try:
            sub = get_config().substrate
            days = float(getattr(sub, "diagnostics_retention_days", 0.0) or 0.0)
            max_mb = getattr(sub, "diagnostics_retention_max_mb", None)
        except Exception:
            return
        if days <= 0 and max_mb is None:
            return
        store = self.diagnostics_store
        if not hasattr(store, "apply_retention"):
            return  # custom sink without a retention surface

        def _sweep() -> None:
            try:
                deleted = store.apply_retention(
                    max_age_days=days if days > 0 else None,
                    max_total_mb=max_mb,
                )
                if deleted.get("records_deleted") or deleted.get("chunks_deleted"):
                    self.logger.info(
                        f"diagnostics retention sweep: {deleted} "
                        f"(policy: {days}d / {max_mb} MB)")
            except Exception as e:
                self.logger.warning(f"diagnostics retention sweep failed: {e}")

        import threading
        threading.Thread(target=_sweep, name="cjm-diagnostics-retention",
                         daemon=True).start()

    def register_system_monitor(
        self,
        capability_name:str # Name of the system monitor capability
    ) -> None:
        """Bind a loaded capability to act as the hardware system monitor."""
        self.system_monitor = self.get_capability(capability_name)
        if self.system_monitor:
            self.logger.info(f"Registered system monitor: {capability_name}")
        else:
            self.logger.warning(f"System monitor capability not found: {capability_name}")

    def _resolve_system_monitor(
        self,
    ) -> Optional[Any]: # The bound system-monitor proxy, or None
        """Return the system monitor, lazily binding from the constructor's
        `sysmon_capability_name` when `register_system_monitor` was never called.

        Stage-3 G11: requiring a SEPARATE `register_system_monitor()` call after
        load was a trap every core CLI fell into — GPU subtree ATTRIBUTION worked
        (the JobQueue queries its own `sysmon_capability_name` directly) while the
        stats path silently returned `{}`, so the scheduler quantity checks AND
        the stage-3 admission ladder saw no telemetry and every GPU-profiled job
        ran exclusive. The constructor parameter now expresses the full intent.
        """
        if self.system_monitor:
            return self.system_monitor
        name = getattr(self, "_sysmon_capability_name", None)
        if name:
            monitor = self.get_capability(name)
            if monitor:
                self.system_monitor = monitor
                self.logger.info(f"System monitor lazily bound from sysmon_capability_name: {name}")
                return monitor
        return None

    def _get_global_stats(self) -> Dict[str, Any]: # Current system telemetry
        """Fetch real-time stats from the system monitor capability (sync).
    
        CR-3: prefer typed `get_system_status()` over magic-string dispatcher.
        Duck-types because the substrate references `system_monitor` as a
        generic `ToolCapability` — CR-1's host-no-imports rule means substrate
        does not import the monitor capability to type-narrow the reference.
        Proxies after CR-3 expose `get_system_status` as a bound method that
        POSTs to `/get_system_status` and returns `Optional[Dict[str, Any]]`.
        """
        monitor = self._resolve_system_monitor()
        if not monitor:
            return {}
        # CR-3: prefer typed call
        get_status = getattr(monitor, "get_system_status", None)
        if callable(get_status):
            try:
                result = get_status()
                if isinstance(result, dict):
                    return result
                if hasattr(result, "to_dict"):  # In-process capability returning SystemStats directly
                    return result.to_dict()
                if result is None:
                    return {}  # Worker unreachable (proxy already logged ConnectError)
                self.logger.warning(
                    f"get_system_status returned unexpected type: {type(result).__name__}"
                )
                return {}
            except Exception as e:
                self.logger.warning(f"Typed get_system_status failed: {e}")
                return {}
        # A configured monitor without the typed get_system_status surface is a
        # configuration error (every migrated monitor exposes it post-CR-3); yield
        # empty stats so admission degrades conservatively rather than crashing.
        self.logger.warning(
            f"system_monitor {type(monitor).__name__} lacks get_system_status; returning empty stats"
        )
        return {}

    async def _get_global_stats_async(self) -> Dict[str, Any]: # Current system telemetry
        """Fetch real-time stats from the system monitor capability (async).
    
        Same CR-3 duck-type semantics as the sync variant. Async variant exists
        because the substrate's `execute_capability_async` path (CR-2 + CR-10) needs
        a non-blocking stats fetch when scheduling under an asyncio event loop.
        """
        monitor = self._resolve_system_monitor()
        if not monitor:
            return {}
        # CR-3: prefer typed call
        get_status_async = getattr(monitor, "get_system_status_async", None)
        if callable(get_status_async):
            try:
                result = await get_status_async()
                if isinstance(result, dict):
                    return result
                if hasattr(result, "to_dict"):
                    return result.to_dict()
                if result is None:
                    return {}  # Worker unreachable (proxy already logged ConnectError)
                self.logger.warning(
                    f"get_system_status_async returned unexpected type: {type(result).__name__}"
                )
                return {}
            except Exception as e:
                self.logger.warning(f"Typed get_system_status_async failed: {e}")
                return {}
        # See the sync variant: a monitor lacking the typed async surface is a
        # configuration error; yield empty stats so admission degrades conservatively.
        self.logger.warning(
            f"system_monitor {type(monitor).__name__} lacks get_system_status_async; returning empty stats"
        )
        return {}

    async def get_global_stats(self) -> Dict[str, Any]: # Current system telemetry
        """Public async system-telemetry accessor (stage 3 / CR-16).

        The JobQueue's multi-lane admission consumes this through the
        `JobQueueDependencies` protocol (defensively via getattr). Thin wrapper
        over `_get_global_stats_async` — same CR-3 duck-type semantics.
        """
        return await self._get_global_stats_async()

    def get_admission_profile(
        self,
        name_or_id:str # Capability name (default instance) or instance_id (multi-instance)
    ) -> Optional[Dict[str, Any]]: # {'gpu_memory_mb_peak_max','memory_mb_peak_max','sample_count'} or None
        """Empirical resource profile for a loaded instance's CURRENT config
        (stage 3 / CR-16 multi-lane admission).

        Reads the CR-7 empirical store at the instance's live
        `(instance_id, config_hash)` key — the SAME keying that records samples,
        so the profile always describes the configuration actually being run
        (a config change = a new hash = no record = the queue runs the job
        EXCLUSIVE until its first measurement run graduates it).

        None = no evidence (instance unknown / store disabled / no record for
        this config). The manifest's `requires_gpu` is deliberately not part of
        this surface — GPU use is an empirical fact, not a declaration (stage-3
        ledger G2).
        """
        inst = self.instances.get(name_or_id)
        if inst is None or self.empirical_store is None or not inst.config_hash:
            return None
        try:
            rec = self.empirical_store.get_record(inst.instance_id, inst.config_hash)
        except Exception as e:
            self.logger.warning(f"get_admission_profile({name_or_id!r}) store read failed: {e}")
            return None
        if rec is None:
            return None
        return {
            "gpu_memory_mb_peak_max": rec.gpu_memory_mb_peak_max,
            "memory_mb_peak_max": rec.memory_mb_peak_max,
            "sample_count": rec.sample_count,
        }

    def get_instance_concurrency_cap(
        self,
        name_or_id:str # Capability name (default instance) or instance_id (multi-instance)
    ) -> Optional[int]: # The instance's SG-33 max_concurrent_requests (None = unset)
        """Per-instance concurrency cap for queue admission (stage 3 / CR-16).

        Surfaces the SG-33 `max_concurrent_requests` setting. The queue treats
        None as 1 (same-worker concurrency is OPT-IN per capability — e.g.
        ffmpeg raises its cap because its sync endpoints run in a threadpool
        and concurrent converts genuinely parallelize as subprocesses; model
        workers stay serial-per-instance).
        """
        inst = self.instances.get(name_or_id)
        return inst.max_concurrent_requests if inst is not None else None

    def _parse_resources(
        self,
        manifest: Dict[str, Any]  # Loaded manifest dict
    ) -> Optional[ResourceRequirements]:
        """Phase 5a: parse the manifest's resources block into a ResourceRequirements."""
        res_dict = manifest.get("resources")
        if not res_dict or not isinstance(res_dict, dict):
            return None
        return ResourceRequirements(
            requires_gpu=bool(res_dict.get("requires_gpu", False)),
            platforms=list(res_dict.get("platforms", [])),
            accelerators=list(res_dict.get("accelerators", [])),
        )

    def discover_manifests(self) -> List[CapabilityMeta]: # List of discovered capability metadata
        """Discover capabilities via JSON manifests in search paths.
    
        CR-8: reads each manifest via `load_manifest`, which parses the v2.0
        nested layout into a typed `ManifestV2`.
        `meta.manifest` is set to a flat-shaped dict view so existing consumers
        (proxy, scheduling, execute path) continue working unchanged; the typed
        `ManifestV2` is also attached as `meta.manifest_v2` so drift detection
        + future typed callers can read `drift_tracking.config_schema_hash`
        without re-parsing.
        """
        self.discovered = []
        self.adapter_manifests = []  # CR-17 pt 2: adapter units discovered beside capabilities
        seen_capabilities = set()
        seen_adapters = set()

        for base_path in self.search_paths:
            if not base_path.exists():
                continue
        
            for manifest_file in base_path.glob("*.json"):
                try:
                    # CR-17 pt 2 (stage 4): adapter manifests are separate
                    # registered units in the same search paths — route by the
                    # "unit" discriminator before capability parsing.
                    with open(manifest_file) as _f:
                        _raw = json.load(_f)
                    if is_adapter_manifest(_raw):
                        am = adapter_manifest_from_dict(_raw)
                        if am.name not in seen_adapters:
                            self.adapter_manifests.append(am)
                            seen_adapters.add(am.name)
                            self.logger.info(
                                f"Discovered adapter manifest: {am.name} "
                                f"(task {am.task_name!r}) from {manifest_file}")
                        continue
                    # CR-8: parse via load_manifest → typed ManifestV2.
                    v2 = load_manifest(manifest_file)
                
                    name = v2.code.name
                    if not name or name in seen_capabilities:
                        continue  # Skip duplicates (local shadows global)
                
                    # Build a flat-shaped dict view for legacy consumers that
                    # access manifest as a plain dict (proxy.py reads
                    # `manifest['python_path']`, scheduling.py reads
                    # `manifest.get('resources', {})`, etc.).
                    nested = manifest_to_dict(v2)
                    manifest = {**nested.get("install", {}), **nested.get("code", {})}
                
                    # CR-8: use the typed resources directly — no re-parse from
                    # the dict needed (load_manifest already did it).
                    resources = v2.code.resources
                
                    # SG-35: `author` + `package_name` removed; `description`
                    # kept and validated by SG-6.
                    meta = CapabilityMeta(
                        name=name,
                        version=v2.code.version or "0.0.0",
                        description=v2.code.description,
                        resources=resources,
                        config_schema=v2.code.config_schema,
                    )
                    meta.manifest = manifest
                    # CR-8: stash typed ManifestV2 so drift detection can read
                    # `drift_tracking.config_schema_hash` at load time without
                    # re-parsing. Dynamic attribute (matches `meta.manifest`'s
                    # existing assignment pattern; CapabilityMeta dataclass doesn't
                    # declare either).
                    meta.manifest_v2 = v2
                
                    self.discovered.append(meta)
                    seen_capabilities.add(name)
                    self.logger.info(f"Discovered manifest: {name} from {manifest_file}")

                except Exception as e:
                    self.logger.error(f"Error loading manifest {manifest_file}: {e}")

        return self.discovered

    def get_adapters_for_task(
        self,
        task_name: str,  # Task name, e.g. "graph-storage"
    ) -> List[AdapterManifest]:  # Discovered adapter units serving the task
        """CR-17 pt 2: the adapter-registry view — discovered adapter manifests for a task."""
        return [a for a in getattr(self, 'adapter_manifests', []) if a.task_name == task_name]

    def check_adapter_compatibility(
        self,
        adapter: Union[str, AdapterManifest],  # Adapter unit name or manifest
        capability_name: str,  # Discovered capability (capability) name
    ) -> Dict[str, Any]:  # Match verdict (see match_protocol_against_surface)
        """CR-17 pt 2: surface-based compatibility verdict (host-side; works against
        UNLOADED capabilities — manifest-vs-manifest, no protocol imports host-side).

        Matches the adapter's recorded protocol members against the capability
        manifest's recorded `structural_surface` (pass-2 Thread 3: the capability
        records only itself; the adapter declares the protocol; the substrate
        matches). A capability without a recorded surface (pre-fracture manifest)
        is NOT compatible until its manifest regenerates — staleness stays visible
        instead of silently mis-answering.
        """
        am = adapter if isinstance(adapter, AdapterManifest) else next(
            (a for a in getattr(self, 'adapter_manifests', []) if a.name == adapter), None)
        if am is None:
            raise ValueError(f"Unknown adapter unit {adapter!r}")
        meta = next((m for m in self.discovered if m.name == capability_name), None)
        if meta is None:
            raise ValueError(
                f"Unknown capability {capability_name!r} (run discover_manifests first)")
        surface = (getattr(meta, 'manifest', None) or {}).get('structural_surface')
        return match_protocol_against_surface(am.protocol_members, surface)

    def get_capabilities_compatible_with(
        self,
        adapter: Union[str, AdapterManifest],  # Adapter unit name or manifest
    ) -> List[str]:  # Discovered capability names whose surface satisfies the protocol
        """CR-17 pt 2: the pass-2 compatibility query, manifest-surface-based."""
        return [m.name for m in self.discovered
                if self.check_adapter_compatibility(adapter, m.name)["compatible"]]

    def _resolve_adapter_specs(
        self,
        capability_meta,  # Capability CapabilityMeta being loaded
        adapters=None,  # Explicit adapter unit names (loud refusal on mismatch); None = auto-bind compatibles
    ) -> List[str]:  # Worker specs "module:ClassName"
        """CR-17 pt 2: resolve which adapter impls bind in-worker at spawn.

        AUTO (adapters=None): every discovered adapter whose protocol members match
        the capability's recorded surface binds silently — binding rides
        `load_capability` with no separate manual call (the G11 lesson: a manual
        registration step no CLI makes is silently inert).

        EXPLICIT (adapters=[names]): each named unit is verified; an incompatible
        pairing REFUSES LOUDLY with the missing members in the message (the CR-17
        negative check).
        """
        from cjm_substrate.core.errors import CapabilityInputError
        discovered = getattr(self, 'adapter_manifests', [])
        surface = (getattr(capability_meta, 'manifest', None) or {}).get('structural_surface')
        if adapters is None:
            specs = []
            for am in discovered:
                verdict = match_protocol_against_surface(am.protocol_members, surface)
                if verdict["compatible"]:
                    specs.append(f"{am.module}:{am.class_name}")
                    self.logger.info(
                        f"Auto-binding adapter {am.name} (task {am.task_name!r}) "
                        f"to {capability_meta.name}")
            return specs
        specs = []
        for name in adapters:
            am = next((a for a in discovered if a.name == name), None)
            if am is None:
                raise CapabilityInputError(
                    f"Unknown adapter unit {name!r} (discovered: "
                    f"{[a.name for a in discovered]})", fields_invalid=["adapters"])
            verdict = match_protocol_against_surface(am.protocol_members, surface)
            if not verdict["compatible"]:
                raise CapabilityInputError(
                    f"Adapter {name!r} (task {am.task_name!r}) is NOT compatible with "
                    f"capability {capability_meta.name!r}: "
                    f"missing methods {verdict['missing_methods']}, "
                    f"missing properties {verdict['missing_properties']}, "
                    f"parameter mismatches {verdict['param_mismatches']}"
                    + (f", reason: {verdict['reason']}" if verdict.get('reason') else ""),
                    fields_invalid=["adapters"])
            specs.append(f"{am.module}:{am.class_name}")
        return specs

    def get_capability_meta(
        self,
        capability_name:str # Name of the capability
    ) -> Optional[CapabilityMeta]: # Capability metadata or None
        """Get metadata for a loaded capability by name."""
        return self.capabilities.get(capability_name)

    def get_discovered_meta(
        self,
        capability_name:str # Name of the capability
    ) -> Optional[CapabilityMeta]: # Capability metadata or None
        """Get metadata for a discovered (not necessarily loaded) capability by name."""
        for meta in self.discovered:
            if meta.name == capability_name:
                return meta
        return None

    def _extract_defaults_from_schema(
        self,
        config_schema:Optional[Dict[str, Any]] # JSON Schema with properties
    ) -> Dict[str, Any]: # Default values extracted from schema
        """Extract default values from a JSON Schema's properties."""
        if not config_schema:
            return {}

        properties = config_schema.get("properties", {})
        defaults = {}

        for field_name, field_schema in properties.items():
            if "default" in field_schema:
                defaults[field_name] = field_schema["default"]

        return defaults

    def _validate_config_against_schema(
        self,
        config:Optional[Dict[str, Any]], # Caller-provided config dict (or None)
        config_schema:Optional[Dict[str, Any]], # Capability's JSON Schema from manifest
        capability_name:str, # For error messages and warnings
        strict:bool=True # Reject unknown keys (default); set False to log+filter
    ) -> Dict[str, Any]: # Validated (possibly filtered) config dict
        """SG-5: validate a config dict against the manifest's `config_schema`
        before forwarding to the capability's `initialize()`.
        """
        if config is None:
            return {}
        if not config_schema or "properties" not in config_schema:
            return dict(config)
    
        valid_keys = set(config_schema["properties"].keys())
        unknown_keys = sorted(set(config) - valid_keys)
    
        if unknown_keys:
            if strict:
                raise CapabilityConfigError(
                    f"Unknown config keys for capability {capability_name!r}: {unknown_keys}. "
                    f"Accepted keys per manifest config_schema: {sorted(valid_keys)}. "
                    f"Pass strict=False to ignore unknown keys (forward-compat).",
                    fields_invalid=unknown_keys,
                    config_class_name=capability_name,
                )
            else:
                self.logger.warning(
                    "%s: ignoring unknown config keys %s (lenient mode)",
                    capability_name, unknown_keys,
                )
                return {k: v for k, v in config.items() if k in valid_keys}
    
        return dict(config)

    def _check_config_schema_drift(
        self,
        proxy:Any, # RemoteCapabilityProxy with a live worker
        capability_meta:CapabilityMeta, # Metadata to flag if drift is detected
    ) -> None:
        """SG-9 + CR-8: compare live worker `/config_schema` to the stored hash.
    
        Reads the stored hash from `capability_meta.manifest_v2.drift_tracking.config_schema_hash`
        (populated by `discover_manifests`). Computes the live hash with
        `compute_config_schema_hash` and compares — drift = hashes differ.
    
        Honors `cfg.substrate.drift_detection` opt-out from `cjm.yaml`: hosts
        that don't want the per-load `/config_schema` HTTP call can disable
        detection there. Default is on.
    
        Test fixtures that stub `meta.manifest = {}` without going through
        `discover_manifests` won't have a `manifest_v2` attribute; the
        `getattr(..., None)` fallback yields `stored_hash=None`, which doesn't
        match any live hash — those tests don't expose a real proxy so the
        drift warning fires harmlessly.
        """
        # CR-8: honor cjm.yaml `substrate.drift_detection: false` opt-out.
        try:
            cfg = get_config()
            if not cfg.substrate.drift_detection:
                return
        except AttributeError:
            # Backward compat: hosts running on a pre-CR-8 CJMConfig without
            # the substrate sub-config. Falls through to default-on behavior.
            pass
    
        try:
            live_schema = proxy.get_config_schema()
        except Exception as e:
            self.logger.debug(
                f"Skipping drift detection for {capability_meta.name}: live schema fetch failed ({e})"
            )
            return
    
        # CR-8: hash-based comparison. The manifest carries the witness hash
        # computed at install/regenerate time; substrate hashes the live schema
        # the same way and compares.
        manifest_v2 = getattr(capability_meta, 'manifest_v2', None)
        stored_hash = (manifest_v2.drift_tracking.config_schema_hash
                       if manifest_v2 is not None else None)
        live_hash = compute_config_schema_hash(live_schema)
    
        if stored_hash != live_hash:
            capability_meta.config_schema_drift = True
            capability_meta.live_config_schema = live_schema
            self.logger.warning(
                f"Config schema drift for {capability_meta.name}: manifest disagrees with live worker. "
                f"Run `cjm-ctl regenerate-manifest {capability_meta.name}` to refresh the manifest."
            )

    def _check_structural_surface_drift(
        self,
        proxy:Any, # RemoteCapabilityProxy with a live worker
        capability_meta:CapabilityMeta, # Metadata to flag if drift is detected
    ) -> None:
        """Pass-2 Thread 3 (stage 2): compare the worker's live-derived structural
        surface to the manifest's stored witness hash — third instance of the
        CR-8 hashed-witness + live-companion idiom (after config_schema and the
        compatibility-transport protocol membership it superseded).

        Stage-4 adapter compatibility matches `required_tool_protocol` against
        the RECORDED surface, so a stale recording silently mis-answers
        compatibility queries — this check is what makes that visible.

        Skips silently when: drift detection is opted out (same cjm.yaml switch
        as config-schema drift); the manifest predates surface recording
        (stored hash None — `regenerate-manifest` adds it); or the worker
        predates the /structural_surface endpoint (proxy returns None).
        """
        try:
            cfg = get_config()
            if not cfg.substrate.drift_detection:
                return
        except AttributeError:
            pass

        manifest_v2 = getattr(capability_meta, 'manifest_v2', None)
        stored_hash = (manifest_v2.drift_tracking.structural_surface_hash
                       if manifest_v2 is not None else None)
        if stored_hash is None:
            return  # pre-surface-era manifest: nothing recorded, not drift

        live_surface = (proxy.get_structural_surface()
                        if hasattr(proxy, 'get_structural_surface') else None)
        if live_surface is None:
            return  # old worker / transport failure: skip, don't guess

        from cjm_substrate.core.manifest_format import compute_structural_surface_hash
        if compute_structural_surface_hash(live_surface) != stored_hash:
            capability_meta.structural_surface_drift = True
            self.logger.warning(
                f"Structural-surface drift for {capability_meta.name}: the installed "
                f"code's public surface disagrees with the manifest recording. Run "
                f"`cjm-ctl regenerate-manifest {capability_meta.name}` to refresh "
                f"(adapter compatibility matches against the recorded surface)."
            )

    def _persist_config(
        self,
        capability_name: str  # Capability to persist
    ) -> None:
        """CR-2: write current CapabilityMeta state + live worker config to the store.
    
        Reads `meta.enabled` (the substrate-authoritative flag) and the worker's
        current_config (when reachable). Failures are logged + swallowed —
        persistence is a best-effort side-channel, not a correctness invariant.
        """
        meta = self.capabilities.get(capability_name)
        if meta is None:
            return
        current_config: Dict[str, Any] = {}
        if meta.instance is not None:
            try:
                fetched = meta.instance.get_current_config()
                if isinstance(fetched, dict):
                    current_config = fetched
            except Exception as e:
                self.logger.debug(
                    f"Could not fetch live config for {capability_name} during persist: {e}"
                )
        try:
            self.config_store.set(capability_name, CapabilityConfigRecord(
                config=current_config,
                enabled=meta.enabled,
            ))
        except Exception as e:
            self.logger.warning(f"Failed to persist config for {capability_name}: {e}")

    def _maybe_fire_disable_hook(
        self,
        name_or_id: str  # instance_id (or legacy capability_name) whose in-flight job just finished
    ) -> None:
        """CR-2 + CR-10: fire deferred on_disable for `name_or_id` if pending.
    
        Idempotent. Resolves via self.instances first; falls back to
        self.capabilities[name].instance for legacy code paths.
        """
        if name_or_id not in self._pending_disable_hooks:
            return
        self._pending_disable_hooks.discard(name_or_id)
        proxy = None
        inst = self.instances.get(name_or_id)
        if inst is not None:
            proxy = inst.proxy
        else:
            meta = self.capabilities.get(name_or_id)
            if meta is not None:
                proxy = meta.instance
        if proxy is None:
            return
        try:
            proxy.on_disable()
        except Exception as e:
            self.logger.warning(f"on_disable() raised for {name_or_id}: {e}")

    def _validate_instance_id(self, instance_id: str) -> None:
        """Reject malformed explicit instance_ids at load time.
    
        Pattern: alphanumeric + underscore + hyphen, length 1..64. Raises
        ValueError on invalid input so the caller sees the constraint failure
        immediately rather than at first execute / unload.
        """
        import re as _re
        if not isinstance(instance_id, str):
            raise ValueError(
                f"instance_id must be str, got {type(instance_id).__name__}"
            )
        if not _re.fullmatch(r"[A-Za-z0-9_-]{1,64}", instance_id):
            raise ValueError(
                f"instance_id {instance_id!r} must match pattern [A-Za-z0-9_-]{{1,64}}"
            )

    def _generate_instance_id(self, capability_name: str) -> str:
        """Generate a unique instance_id of form `{capability_name}-{6-char-hex}`.
    
        Used when load_capability is called with new_instance=True and no explicit
        instance_id. Retries up to 16 times if a collision occurs in self.instances.
        """
        import secrets as _secrets
        for _ in range(16):
            candidate = f"{capability_name}-{_secrets.token_hex(3)}"
            if candidate not in self.instances:
                return candidate
        raise RuntimeError(
            f"Could not generate unique instance_id for {capability_name!r} after 16 attempts"
        )

    def get_instance(
        self,
        name_or_id: str  # Capability name (default-loaded) or explicit instance_id
    ) -> Optional[CapabilityInstance]:
        """Return the CapabilityInstance for `name_or_id`, or None if not loaded.
    
        Lookup is keyed by instance_id (which equals capability_name for default-
        loaded capabilities). Multi-instance IDs only exist in self.instances.
        """
        return self.instances.get(name_or_id)

    def list_instances(
        self,
        capability_name: Optional[str] = None  # If given, filter to this capability's instances
    ) -> List[CapabilityInstance]:
        """List all loaded instances, optionally filtered by underlying capability name."""
        if capability_name is None:
            return list(self.instances.values())
        return [i for i in self.instances.values() if i.capability_name == capability_name]

    # ------------------------------------------------------------------
    # CR-12: worker-environment overlay composition (secrets + visible vars)
    # ------------------------------------------------------------------
    def _worker_env_specs(
        self,
        capability_meta: CapabilityMeta  # Capability whose WORKER_ENV contract to read
    ) -> List[Dict[str, Any]]:  # List of EnvVarSpec-as-dict entries (possibly empty)
        """Return a capability's WORKER_ENV contract as spec dicts (CR-12).

        Prefers the typed manifest_v2 code section; falls back to the flat manifest
        dict view. Empty list when the capability declares no worker-env contract.
        """
        mv2 = getattr(capability_meta, "manifest_v2", None)
        if mv2 is not None and getattr(mv2.code, "worker_env", None):
            return list(mv2.code.worker_env)
        flat = getattr(capability_meta, "manifest", None) or {}
        return list(flat.get("worker_env") or [])

    def _resolve_worker_env(
        self,
        capability_meta: CapabilityMeta,        # Capability being loaded
        scope: Optional[str] = None     # SG-55 forward seam: per-principal scope (None = single-user)
    ) -> Dict[str, str]:  # {ENV_NAME: value} overlay injected into the worker at spawn
        """CR-12 + Q1-A: compose the resolved worker-env overlay for a load.

        Secrets resolve from the SecretStore keyed by capability_name — so every
        instance of a capability shares one credential (CR-10: two Gemini instances,
        one GEMINI_API_KEY). A missing secret is OMITTED (the worker spawns without
        it; the capability reports the gap at execute) rather than injected empty.

        Visible vars resolve from their declared `default`, with Q1-A template
        substitution applied: a default like ``"${CJM_MODELS_DIR}/huggingface"``
        expands to an absolute path using the substrate's current `cfg.models_dir`
        + `cfg.capability_data_dir`. Static defaults (no `${...}` syntax) pass through
        unchanged. A template-substitution failure — unknown placeholder (capability
        author bug) OR unresolved value (operator hasn't configured
        `cfg.models_dir`) — is WARN-and-OMIT: the worker still spawns, and the
        capability can surface the gap via `missing_required_env()` if the field was
        declared `required=True`. This matches secret omission behaviour
        (operator-side concerns don't break load; the capability signals at execute).
        Capability-author-bug-class errors (unknown placeholders) surface at
        install/release time via `cjm-ctl validate` + `template_check_placeholders`,
        not here. All values are fixed at spawn — a change requires `reload_capability`.
        """
        from cjm_substrate.core.capability import expand_worker_env_template

        cfg = get_config()
        # Build the placeholder context once per load. The substrate is the
        # source of truth for CJM_*_DIR; CAPABILITY_DATA_DIR is conventionally
        # `<cfg.capability_data_dir>/<capability_name>` (matches the per-capability data
        # subdirectory each capability's meta.py traditionally computed).
        placeholders: Dict[str, Optional[str]] = {
            "CJM_MODELS_DIR": str(cfg.models_dir) if cfg.models_dir else None,
            "CJM_CAPABILITY_DATA_DIR": str(cfg.capability_data_dir) if cfg.capability_data_dir else None,
            "CAPABILITY_DATA_DIR": (
                str(cfg.capability_data_dir / capability_meta.name) if cfg.capability_data_dir else None
            ),
            "CAPABILITY_NAME": capability_meta.name,
        }

        overlay: Dict[str, str] = {}
        for spec in self._worker_env_specs(capability_meta):
            name = spec.get("name")
            if not name:
                continue
            if spec.get("secret"):
                try:
                    val = self.secret_store.get_secret(capability_meta.name, name, scope=scope)
                except Exception as e:
                    self.logger.warning(
                        f"secret_store.get_secret({capability_meta.name!r}, {name!r}) failed: {e}"
                    )
                    val = None
                if val is not None:
                    overlay[name] = val
            else:
                default = spec.get("default")
                if default is None:
                    continue
                try:
                    overlay[name] = expand_worker_env_template(
                        str(default),
                        placeholders,
                        capability_name=capability_meta.name,
                        var_name=name,
                    )
                except Exception as e:
                    # WARN + OMIT (matches secret omission shape). The capability's
                    # missing_required_env() check will surface this at execute
                    # time if the field was required; cjm-ctl validate catches
                    # capability-author-bug-class errors (unknown placeholders) at
                    # install/release time via template_check_placeholders.
                    self.logger.warning(
                        f"failed to expand worker-env default for "
                        f"{capability_meta.name!r} EnvVarSpec(name={name!r}, default={default!r}): {e}"
                    )
        return overlay

    def get_worker_env_status(
        self,
        name_or_meta: Any,              # Capability name (loaded/discovered) or a CapabilityMeta
        scope: Optional[str] = None     # SG-55 forward seam
    ) -> List[Dict[str, Any]]:  # Per-entry status dicts (secret values never returned)
        """CR-12: per-entry satisfaction status of a capability's worker-env contract.

        Each entry: {name, secret, required, satisfied, label, description}.
        `satisfied` means a value is resolvable (secret present in the store, or a
        visible var has a default/override). Secret VALUES are never returned — only
        whether one is set. The capability-config UI uses this to gate config display on
        required secrets being satisfied.
        """
        meta = name_or_meta if not isinstance(name_or_meta, str) else (
            self.capabilities.get(name_or_meta) or self.get_discovered_meta(name_or_meta)
        )
        out: List[Dict[str, Any]] = []
        if meta is None:
            return out
        for spec in self._worker_env_specs(meta):
            name = spec.get("name")
            if not name:
                continue
            if spec.get("secret"):
                try:
                    satisfied = self.secret_store.get_secret(meta.name, name, scope=scope) is not None
                except Exception:
                    satisfied = False
            else:
                satisfied = spec.get("default") is not None  # + future operator-override store
            out.append({
                "name": name,
                "secret": bool(spec.get("secret")),
                "required": bool(spec.get("required")),
                "satisfied": satisfied,
                "label": spec.get("label", ""),
                "description": spec.get("description", ""),
            })
        return out

    def missing_required_env(
        self,
        name_or_meta: Any,              # Capability name or CapabilityMeta
        scope: Optional[str] = None     # SG-55 forward seam
    ) -> List[str]:  # Names of required worker-env entries with no resolvable value
        """CR-12: names of required worker-env entries that are unsatisfied."""
        return [
            s["name"] for s in self.get_worker_env_status(name_or_meta, scope=scope)
            if s["required"] and not s["satisfied"]
        ]

    def set_capability_secret(
        self,
        name_or_id: str,             # Capability name or instance_id whose secret to set
        key: str,                    # Secret key (the env-var name, e.g. "GEMINI_API_KEY")
        value: str,                  # Secret value (stored via the SecretStore, never config/logs)
        *,
        scope: Optional[str] = None, # SG-55 forward seam: per-principal scope
        reload: bool = True          # Respawn loaded worker(s) so the new env is injected
    ) -> bool:  # True if the secret was stored
        """CR-12: store a capability secret, then respawn its worker(s) to inject it.

        Secrets are keyed by the underlying CAPABILITY name (not instance_id), so all
        instances of a capability share one credential — set the Gemini key once and
        every Gemini instance gets it at (re)spawn. Because worker env is fixed at
        spawn, the new value only reaches a *running* worker via a RESPAWN, so this
        reloads each loaded instance of the capability (unless `reload=False`, e.g. when
        provisioning a secret before the capability is loaded). This is the
        actuation seam both the CLI (`cjm-ctl set-secret`) and a future config UI
        call. Reload failures are logged, not raised.
        """
        inst = self.instances.get(name_or_id)
        capability_name = inst.capability_name if inst is not None else name_or_id
        self.secret_store.set_secret(capability_name, key, value, scope=scope)
        if not reload:
            return True
        targets = [i.instance_id for i in self.instances.values() if i.capability_name == capability_name]
        for iid in targets:
            try:
                self.reload_capability(iid)
            except Exception as e:
                self.logger.warning(f"set_capability_secret: reload of {iid!r} failed: {e}")
        return True

    def load_capability(
        self,
        capability_meta:CapabilityMeta, # Capability metadata (with manifest attached)
        config:Optional[Dict[str, Any]]=None, # Initial configuration
        strict:bool=True, # SG-5: reject unknown keys against manifest config_schema (default)
        instance_id:Optional[str]=None, # CR-10: explicit instance_id; None defaults to capability_name
        new_instance:bool=False, # CR-10: auto-generate `{name}-{hex}` instance_id (with instance_id=None)
        max_concurrent_requests:Optional[int]=None, # SG-33 (CR-7): per-instance async concurrency cap; None = unbounded
        adapters:Optional[List[str]]=None # CR-17 pt 2: explicit adapter unit names (loud refusal on mismatch); None = auto-bind discovered compatibles
    ) -> bool: # True if successfully loaded
        """Load a capability by spawning a Worker subprocess.
    
        CR-2: reads the persisted CapabilityConfigRecord from `self.config_store`
        before launching the worker. If a persisted record exists and the
        caller didn't pass an explicit config, the persisted config is used
        as the effective input. The persisted `enabled` flag is applied to
        `capability_meta.enabled` so disabled capabilities stay disabled across
        process restarts.
    
        CR-10: optional `instance_id` allows multi-instance loading.
        - instance_id=None, new_instance=False (default): instance_id =
          capability_meta.name. Populates self.capabilities[capability_name] + self.instances
          [capability_name] together (single-instance backward compat).
        - instance_id="custom": validated against `[A-Za-z0-9_-]{1,64}`. Populates
          self.instances[custom]. Persistence is keyed by capability_name and only
          applied to the default instance.
        - instance_id=None, new_instance=True: auto-generates `{name}-{6-hex}`.
        Idempotent: re-load against an existing instance_id returns True without
        re-spawning.
    
        CR-7: computes `config_hash` from the effective config (post-defaults +
        post-validation) and stores it on the CapabilityInstance so execute_capability*
        can key empirical samples by (instance_id, config_hash). SG-33 stores
        `max_concurrent_requests` on the instance — the actual asyncio.Semaphore
        is lazy-created in execute_capability_async via `_get_concurrent_limiter`.
        """
        if not hasattr(capability_meta, 'manifest'):
            self.logger.error(f"Capability {capability_meta.name} has no manifest data")
            return False
    
        # CR-10: resolve instance_id + idempotency check
        if instance_id is None:
            resolved_id = self._generate_instance_id(capability_meta.name) if new_instance else capability_meta.name
        else:
            self._validate_instance_id(instance_id)
            resolved_id = instance_id
        if resolved_id in self.instances:
            self.logger.info(f"Instance {resolved_id!r} already loaded; idempotent skip")
            return True
        is_default = (resolved_id == capability_meta.name)

        # CR-2: read persisted record (config + enabled flag) before launching.
        # Persistence is per-capability (keyed by capability_name), not per-instance, so
        # multi-instance loads ignore the persisted state.
        persisted: Optional[CapabilityConfigRecord] = None
        if is_default:
            try:
                persisted = self.config_store.get(capability_meta.name)
            except Exception as e:
                self.logger.debug(
                    f"config_store.get({capability_meta.name}) raised; falling through: {e}"
                )

        try:
            self.logger.info(
                f"Launching worker for {capability_meta.name} (instance_id={resolved_id})..."
            )
            # CR-12: resolve the worker-env overlay (secrets from the SecretStore +
            # visible defaults) and inject it at spawn. Warn — don't fail — when a
            # required secret is unset: the capability loads lazily and reports the gap
            # at execute, so a config UI / operator can supply the secret post-load.
            extra_env = self._resolve_worker_env(capability_meta)
            _missing_env = self.missing_required_env(capability_meta)
            if _missing_env:
                self.logger.warning(
                    f"{capability_meta.name}: required worker-env unsatisfied {_missing_env}; "
                    f"capability loads but can't do useful work until set "
                    f"(e.g. `cjm-ctl set-secret {capability_meta.name} <KEY>`)."
                )
            # CR-17 pt 2: resolve adapter impls (auto-bind compatibles, or verify
            # the explicit list with loud refusal) and bind them in-worker at spawn.
            adapter_specs = self._resolve_adapter_specs(capability_meta, adapters)
            proxy = RemoteCapabilityProxy(capability_meta.manifest, extra_env=extra_env,
                                      adapter_specs=adapter_specs,
                                      journal=self.journal_store,
                                      diagnostics=self.diagnostics_store)

            config_schema = capability_meta.manifest.get("config_schema")
        
            # SG-9 + CR-8: detect drift between manifest-stored schema hash and
            # live worker. Drift check reads the stored hash from
            # `capability_meta.manifest_v2.drift_tracking.config_schema_hash` and
            # honors `cfg.substrate.drift_detection` opt-out internally.
            self._check_config_schema_drift(proxy, capability_meta)
            # Pass-2 Thread 3 companion: structural-surface drift (same idiom,
            # same cjm.yaml opt-out switch).
            self._check_structural_surface_drift(proxy, capability_meta)
        
            # CR-2: effective config = caller > persisted (default-only) > manifest defaults.
            if not config and persisted is not None and persisted.config:
                config = dict(persisted.config)
                self.logger.info(
                    f"Using persisted config for {capability_meta.name}: {list(config.keys())}"
                )
        
            # If config is still None or empty, extract defaults from the
            # capability's config schema (existing behavior).
            if not config:
                config = self._extract_defaults_from_schema(config_schema)
                if config:
                    self.logger.info(f"Using default config for {capability_meta.name}: {list(config.keys())}")
            else:
                # SG-5: validate caller-provided / persisted config against
                # manifest schema before forwarding to the worker.
                config = self._validate_config_against_schema(
                    config, config_schema, capability_meta.name, strict=strict,
                )

            # Initialize with config (defaults or provided)
            if config:
                proxy.initialize(config)

            # CR-10: per-instance enabled flag. Default instance restores from
            # persistence; multi-instance starts enabled.
            effective_enabled = True
            if is_default and persisted is not None:
                effective_enabled = persisted.enabled
                capability_meta.enabled = effective_enabled
        
            # CR-7: hash the effective config so empirical recording can key by
            # (instance_id, config_hash). Two configs for the same instance get
            # two distinct records (e.g. whisper at model=base vs model=large).
            effective_config = dict(config or {})
            instance_config_hash = compute_config_hash(effective_config)
        
            # CR-10: always record the per-instance state
            self.instances[resolved_id] = CapabilityInstance(
                instance_id=resolved_id,
                capability_name=capability_meta.name,
                config=effective_config,
                proxy=proxy,
                enabled=effective_enabled,
                config_hash=instance_config_hash,
                max_concurrent_requests=max_concurrent_requests,
            )
        
            # Default-instance only: maintain backward-compat single-instance
            # references (CapabilityMeta.instance, self.capabilities[capability_name]).
            if is_default:
                capability_meta.instance = proxy
                self.capabilities[capability_meta.name] = capability_meta
            elif capability_meta.name not in self.capabilities:
                # First-ever instance for this capability is multi-instance — record
                # the CapabilityMeta so list_capabilities / get_capability_meta still work,
                # but leave meta.instance=None (no canonical instance exists).
                self.capabilities[capability_meta.name] = capability_meta
        
            self.logger.info(
                f"Loaded capability: {capability_meta.name} "
                f"(instance_id={resolved_id}, enabled={effective_enabled})"
            )

            # CR-14: the effective config at load is a journal event — derived at
            # the substrate boundary (config KEY NAMES only + the hash; readable
            # config lives in run manifests per the I8 rider — never duplicate).
            # Defensive getattr: test fixtures construct via __new__ without the
            # observability stores (the CR-7 fixture pattern).
            _journal = getattr(self, 'journal_store', None)
            if _journal is not None:
                _journal.append(JournalEvent(
                    event_type=SubstrateEventType.CONFIG_APPLIED.value,
                    capability_instance_id=resolved_id,
                    capability_name=capability_meta.name,
                    config_hash=instance_config_hash,
                    worker_session_id=getattr(proxy, 'worker_session_id', None),
                    payload={"phase": "load", "config_keys": sorted(effective_config),
                             "enabled": effective_enabled},
                ))
            return True

        except Exception as e:
            self.logger.error(
                f"Failed to load capability {capability_meta.name} (instance_id={resolved_id}): {e}"
            )
            return False

    def load_all(
        self,
        configs:Optional[Dict[str, Dict[str, Any]]]=None # Capability name -> config mapping
    ) -> Dict[str, bool]: # Capability name -> success mapping
        """Discover and load all available capabilities."""
        configs = configs or {}
        results = {}
    
        self.discover_manifests()
        for meta in self.discovered:
            config = configs.get(meta.name)
            results[meta.name] = self.load_capability(meta, config)
    
        return results

    def unload_capability(
        self,
        name_or_id:str # Capability name (default-loaded) or instance_id (multi-instance)
    ) -> bool: # True if successfully unloaded
        """Unload a capability instance and terminate its Worker process (CR-10).
    
        If name_or_id resolves to the default instance (instance_id == capability_name)
        and no other instances remain for the same capability, also removes the
        CapabilityMeta from self.capabilities. Otherwise removes only the instance and
        clears CapabilityMeta.instance if it pointed at the unloaded canonical.
        """
        inst = self.instances.get(name_or_id)
        if inst is None:
            self.logger.error(f"Capability/instance {name_or_id!r} not found")
            return False
        capability_name = inst.capability_name
        instance_id = inst.instance_id
        try:
            if inst.proxy is not None:
                inst.proxy.cleanup()
            del self.instances[instance_id]
            self._pending_disable_hooks.discard(instance_id)
            self._running_executions.discard(instance_id)
            # SG-33 (CR-7): drop the lazy concurrency limiter — it would otherwise
            # leak (and be stale if the same instance_id gets reloaded with a
            # different max_concurrent_requests setting). Defensive against test
            # fixtures that bypass __init__ and don't have _concurrent_limiters.
            _limiters = getattr(self, '_concurrent_limiters', None)
            if _limiters is not None:
                _limiters.pop(instance_id, None)
            # Backward-compat: also clear capability_name keys for the canonical instance
            self._pending_disable_hooks.discard(capability_name)
            self._running_executions.discard(capability_name)
        
            remaining = [i for i in self.instances.values() if i.capability_name == capability_name]
            if not remaining:
                # No instances of this capability at all (whether the unloaded one
                # was canonical or multi-instance) — drop the CapabilityMeta entry.
                self.capabilities.pop(capability_name, None)
            elif instance_id == capability_name:
                # Canonical instance unloaded but multi-instances remain — clear
                # the now-stale canonical reference; CapabilityMeta stays so
                # list_capabilities / get_capability_meta still surface the capability.
                meta = self.capabilities.get(capability_name)
                if meta is not None:
                    meta.instance = None
            self.logger.info(f"Unloaded capability: {capability_name} (instance_id={instance_id})")
            return True
        except Exception as e:
            self.logger.error(f"Error unloading {name_or_id!r}: {e}")
            return False

    def unload_all(self) -> None:
        """Unload all capability instances and terminate all Worker processes (CR-10).
    
        Iterates self.instances (CR-10 keying) rather than self.capabilities so all
        multi-instance entries get torn down, not just the canonical instances.
        """
        for inst_id in list(self.instances.keys()):
            self.unload_capability(inst_id)
        # Catch any legacy capability entries that didn't have a corresponding instance
        # (shouldn't happen post-CR-10 but defensive cleanup)
        for name in list(self.capabilities.keys()):
            self.unload_capability(name)

    def get_capability(
        self,
        name_or_id:str # Capability name (default-loaded) or instance_id (multi-instance)
    ) -> Optional[ToolCapability]: # Capability proxy instance or None
        """Get a loaded capability's proxy by name or instance_id (CR-10).
    
        Lookup order: self.instances first (covers both default capability_name and
        multi-instance IDs), falling back to CapabilityMeta.instance for any
        legacy code path that populated self.capabilities without self.instances
        (defensive — shouldn't happen post-CR-10 since load_capability always
        records the instance).
        """
        inst = self.instances.get(name_or_id)
        if inst is not None:
            return inst.proxy
        meta = self.capabilities.get(name_or_id)
        return meta.instance if meta else None

    def list_capabilities(self) -> List[CapabilityMeta]: # List of loaded capability metadata
        """List all loaded capabilities."""
        return list(self.capabilities.values())

    def _get_sysmon_capability(self) -> Optional[Any]:
        """Resolve the configured monitor capability (CR-3) for GPU subtree attribution.

        Returns the loaded capability instance keyed by `sysmon_capability_name`, or
        None when no sysmon is configured / hasn't been loaded yet. Lazy
        resolution against `self.capabilities` tolerates load-order: the manager
        can be constructed before the sysmon capability is loaded; later
        `_record_sample_safe` calls pick it up automatically.
        """
        name = getattr(self, "_sysmon_capability_name", None)
        if not name:
            return None
        meta = self.capabilities.get(name)
        return getattr(meta, "instance", None) if meta else None

    def _record_sample_safe(self, inst:CapabilityInstance, start_time:float, success:bool) -> None:
        """CR-7: best-effort empirical sample recording.

        Captures worker stats at end-of-execute (proxy of peak), builds a
        ResourceSample, and records it via the EmpiricalResourceStore. Failures
        log + swallow — sample recording must never break the execute path
        (matches CR-2's `_persist_config` best-effort discipline).

        Stats fetch can fail naturally (e.g. worker died with WorkerOOMError —
        the proxy is unreachable). The sample still records with zero stats +
        success=False so we have a record of the failed attempt for the
        success_rate aggregate.

        GPU memory is attributed across the worker's process subtree via
        `attribute_gpu_to_worker_subtree` (intersecting worker-reported
        `subtree_pids` with sysmon's per-PID GPU enumeration). Pre-fix this
        function read `worker_stats["gpu_memory_mb"]` — a key the worker `/stats`
        endpoint NEVER emits — so EmpiricalResourceRecord.gpu_memory_mb_peak_max
        was silently 0 for every capability since CR-7 shipped, not just for
        subprocess-spawning ones. When no sysmon is configured, GPU memory
        records as 0.0 (honest signal that we can't measure it).
        """
        # Defensive against test fixtures that bypass __init__: if empirical_store
        # wasn't initialized, the substrate just skips recording. Same pattern
        # CR-8 uses for `manifest_v2` in `_check_config_schema_drift`.
        store = getattr(self, 'empirical_store', None)
        if store is None:
            return
        if not inst.config_hash:
            # No config_hash means the instance wasn't loaded through load_capability
            # (test fixtures with manual self.instances[...] = CapabilityInstance(...)
            # populate). Skip recording rather than keying records by empty string.
            return
        try:
            worker_stats: Dict[str, Any] = {}
            if inst.proxy is not None:
                # The stats FETCH is a sync HTTP round-trip to the worker (~10ms) —
                # per-op it dominates ms-scale ops 9:1, so it rides a short TTL cache
                # (the queue's resource_snapshot_cadence_polls precedent). duration/
                # success below stay exact per-op; cpu/mem peaks are ≤1Hz samples.
                cache = getattr(self, '_worker_stats_cache', None)
                if cache is None:
                    cache = self._worker_stats_cache = {}
                cached = cache.get(inst.instance_id)
                now = time.monotonic()
                if cached is not None and (now - cached[0]) < 1.0:
                    worker_stats = cached[1]
                else:
                    try:
                        fetched = inst.proxy.get_stats()
                        if isinstance(fetched, dict):
                            worker_stats = fetched
                            cache[inst.instance_id] = (now, fetched)
                    except Exception:
                        # Worker may be dead (WorkerOOMError path). Sample with zero stats.
                        pass

            # GPU subtree attribution via the shared helper. Returns None when no
            # sysmon is configured / reachable; returns 0.0 for CPU-only capabilities.
            gpu_mb = 0.0
            sysmon = self._get_sysmon_capability() if hasattr(self, '_get_sysmon_capability') else None
            if sysmon is not None and worker_stats:
                attribution = attribute_gpu_to_worker_subtree(worker_stats, sysmon)
                if attribution is not None:
                    gpu_mb = float(attribution.get('gpu_memory_mb') or 0.0)

            duration = max(0.0, time.time() - start_time)
            # SG-54: fold capability-reported measured usage (unit-agnostic) into the sample.
            _usage = worker_stats.get("usage")
            api_usage = {k: float(v) for k, v in _usage.items()} if isinstance(_usage, dict) and _usage else None
            sample = ResourceSample(
                cpu_percent=float(worker_stats.get("cpu_percent", 0.0) or 0.0),
                memory_mb_peak=float(worker_stats.get("memory_rss_mb", 0.0) or 0.0),
                gpu_memory_mb_peak=gpu_mb,
                duration_seconds=duration,
                success=success,
                observed_at=datetime.now(timezone.utc),
                api_usage=api_usage,
            )
            store.record_sample(
                inst.instance_id, inst.capability_name, inst.config_hash, sample,
            )
        except Exception as e:
            self.logger.warning(
                f"CR-7: empirical sample recording failed for {inst.instance_id}: {e}"
            )

    def _get_concurrent_limiter(self, instance_id:str) -> Optional[asyncio.Semaphore]:
        """SG-33 (CR-7): lazy-create the per-instance asyncio.Semaphore.
    
        Returns None when the instance has no `max_concurrent_requests` set (the
        default — unbounded). Otherwise creates the semaphore on first call and
        caches it in `self._concurrent_limiters`. Semaphores are bound to the
        event loop they were created in; lazy creation inside `execute_capability_async`
        ensures we're inside the right loop at construction time (Python 3.10+
        semaphore-loop-binding rules).
    
        Defensive: returns None if the manager was constructed via __new__ without
        `_concurrent_limiters` being populated (test-fixture pattern).
        """
        limiters = getattr(self, '_concurrent_limiters', None)
        if limiters is None:
            return None
        inst = self.instances.get(instance_id)
        if inst is None or inst.max_concurrent_requests is None:
            return None
        limiter = limiters.get(instance_id)
        if limiter is None:
            limiter = asyncio.Semaphore(inst.max_concurrent_requests)
            limiters[instance_id] = limiter
        return limiter

    def _reactive_evict_for(
        self,
        needed_meta:CapabilityMeta,
        shortfall:Optional[Any]=None,  # ResourceShortfall from the failed execute (CUDA reports the MARGINAL allocation)
        needy_instance_id:Optional[str]=None,  # The failing instance: empirical-need lookup + candidate exclusion
    ) -> bool:  # True when at least one resident was evicted
        """CR-7: free GPU room after a CapabilityResourceError during execute (335023d6).

        Pre-fix this delegated to _evict_for_resources, which (a) drew candidates
        from the per-NAME meta map, so CR-10 NAMED instances — the live case's
        resident whisper family — were INVISIBLE to eviction, and (b) stopped on
        scheduler.allocate(), whose quantity checks are dead against v2 manifests
        (76441c91): ONE eviction of a possibly zero-GPU candidate "succeeded"
        while ~21GB of idle models stayed resident and the retry re-OOMed.

        Now: candidates are ALL loaded instances except the needy one and any
        mid-execute; a candidate's footprint is its empirical GPU peak (unmeasured
        = nothing known to free = skipped); eviction runs LARGEST-FIRST and stops
        once the estimated freed total covers TARGET = max(the needy instance's
        empirical GPU peak, shortfall.needed). When the needy instance is
        UNPROFILED (its first, measurement run — the live case) the target is
        unknowable, so EVERY idle GPU resident goes: the retry deserves a clean
        GPU, and evicted residents lazy-reload on their next use.
        """
        store = getattr(self, 'empirical_store', None)
        reported = float(getattr(shortfall, 'needed', 0.0) or 0.0)
        available = float(getattr(shortfall, 'available', 0.0) or 0.0)
        needy_rec = None
        if store is not None and needy_instance_id:
            needy_inst = self.instances.get(needy_instance_id)
            if needy_inst is not None and getattr(needy_inst, 'config_hash', None):
                try:
                    needy_rec = store.get_record(needy_inst.instance_id,
                                                 needy_inst.config_hash)
                except Exception:
                    needy_rec = None
        # A record built ONLY from failed attempts cannot size a target: its peak
        # is how far the load got BEFORE the OOM (stress-3 live case: 5184MB
        # recorded vs whisper-large's real ~9.9GB) — treat as unprofiled.
        needy_peak = (float(needy_rec.gpu_memory_mb_peak_max)
                      if needy_rec is not None
                      and float(getattr(needy_rec, 'success_rate', 0.0) or 0.0) > 0.0
                      else 0.0)
        # Freed VRAM ADDS to what was already free at the OOM, and fresh workers
        # carry non-PyTorch overhead — 1.25x margin keeps the retry off the exact
        # boundary (stress-3: freeing exactly-the-peak left the retry ~600MB short).
        target = (max(needy_peak * 1.25 - available, reported, 1.0)
                  if needy_peak > 0.0 else None)
        self.logger.info(
            f"CR-7 reactive eviction for {needed_meta.name} "
            f"(instance={needy_instance_id!r}, reported shortfall={reported:.0f}MB, "
            f"target={f'{target:.0f}MB' if target is not None else 'ALL idle GPU residents (needy unprofiled)'})"
        )

        freed, evicted = self._evict_gpu_residents(
            target, exclude={needy_instance_id} if needy_instance_id else None)
        if evicted == 0:
            self.logger.warning(
                f"CR-7 reactive eviction for {needed_meta.name}: "
                f"no idle GPU-resident candidates to evict"
            )
        return evicted > 0

    def _evict_gpu_residents(
        self,
        target:Optional[float],       # MB to free (None = evict EVERY idle GPU resident)
        exclude:Optional[Set[str]]=None,  # instance_ids never to touch (needy / queue-known busy or pending-targeted)
    ) -> Tuple[float, int]:  # (estimated MB freed, residents evicted)
        """The shared largest-first idle-GPU evict loop (CR-7 + eviction-v2 A).

        Candidates are ALL loaded instances except `exclude` and any mid-execute;
        a candidate's footprint is its empirical GPU peak (unmeasured = nothing
        known to free = skipped). Eviction runs LARGEST-FIRST and stops once the
        estimated freed total covers `target` (None = clean sweep). Extracted
        from _reactive_evict_for so admission-side eviction (evict_idle_gpu,
        ratified 9b0c8eb1) pulls the identical lever from its new call site."""
        store = getattr(self, 'empirical_store', None)

        def _gpu_peak(inst) -> float:
            if store is None or inst is None or not getattr(inst, 'config_hash', None):
                return 0.0
            try:
                rec = store.get_record(inst.instance_id, inst.config_hash)
            except Exception:
                return 0.0
            return float(rec.gpu_memory_mb_peak_max) if rec is not None else 0.0

        exclude = exclude or set()
        candidates = []
        for inst in self.instances.values():
            if inst.instance_id in exclude:
                continue
            if inst.instance_id in self._running_executions:
                continue  # never evict a mid-execute instance
            if inst.proxy is None:
                continue  # not actually resident
            peak = _gpu_peak(inst)
            if peak <= 0.0:
                continue  # nothing known to free on the GPU
            candidates.append((peak, inst))
        candidates.sort(key=lambda pair: -pair[0])

        freed = 0.0
        evicted = 0
        for peak, inst in candidates:
            self.logger.info(
                f"Evicting idle GPU resident: {inst.instance_id} "
                f"(~{peak:.0f}MB, last used {inst.last_executed})"
            )
            if hasattr(inst.proxy, 'release'):
                inst.proxy.release()
            else:
                # Reload preserves the instance's effective config (a named
                # (capability, MODEL) instance must not reset to defaults).
                self.reload_capability(inst.instance_id,
                                       config=dict(inst.config) if inst.config else None)
            time.sleep(0.5)  # release settle (mirrors the pre-fix pacing)
            freed += peak
            evicted += 1
            if target is not None and freed >= target:
                break
        return freed, evicted

    def evict_idle_gpu(
        self,
        shortfall_mb:float,                             # VRAM the blocked job lacks (empirical peak - live free)
        exclude_instance_ids:Optional[List[str]]=None,  # Queue-known in-flight + pending-targeted instances
    ) -> float:  # Estimated MB freed (0.0 when no candidate qualified)
        """Admission-side idle eviction (eviction-v2 A, ratified 9b0c8eb1).

        The queue's resources rung fires this (post-lock task, fire-and-re-scan)
        when a profiled job fits the admission BUDGET but not live FREE VRAM —
        the c5bbd511 deadlock: idle residents never release on their own, and
        the CR-7 reactive backstop can't fire on a job that never dispatches.
        The target carries the same 1.25x margin as the reactive path (20b5689:
        freeing exactly the shortfall leaves the load on the boundary). Victim
        hysteresis is STRUCTURAL — the exclude list carries the queue's
        in-flight + pending-targeted instances; a time-based window is
        deliberately absent from v1 (it would have refused the live repro's
        only useful victims — see the ratification)."""
        target = max(float(shortfall_mb) * 1.25, 1.0)
        exclude = set(exclude_instance_ids or ())
        self.logger.info(
            f"Admission idle-eviction request: shortfall {shortfall_mb:.0f}MB "
            f"(target {target:.0f}MB, excluding {len(exclude)} instance(s))"
        )
        freed, evicted = self._evict_gpu_residents(target, exclude=exclude)
        if evicted == 0:
            self.logger.warning(
                "Admission idle-eviction: no idle GPU-resident candidates to evict"
            )
        return freed

    def _evict_for_resources(self, needed_meta:CapabilityMeta) -> bool:
        """Attempt to free resources by unloading/releasing idle capabilities (LRU).
    
        CR-7: extended from GPU-only LRU to multi-axis cost-aware eviction.
        - Candidate set: any loaded capability that isn't the one we're allocating
          for (drops the pre-CR-7 `requires_gpu` filter).
        - Sort key: primary = idle (older last_executed first, classic LRU);
          secondary = empirical cost when available (highest peak gets evicted
          first among equally-idle candidates). Cost axis follows the needed
          capability's `resources.requires_gpu` flag — GPU peak when we're freeing
          for a GPU capability, system memory peak otherwise.
    
        Without empirical data (no store / unmeasured capability), the secondary
        key is 0.0 and pure LRU applies. Cost-aware selection is opt-in via
        `empirical_tracking: true`.
        """
        self.logger.info(f"Attempting eviction to make room for {needed_meta.name}...")
    
        candidates = [
            meta for name, meta in self.capabilities.items()
            if meta.instance is not None and name != needed_meta.name
        ]
    
        needs_gpu = bool(
            needed_meta.resources is not None and needed_meta.resources.requires_gpu
        )
        store = getattr(self, 'empirical_store', None)
    
        def _eviction_priority(candidate_meta:CapabilityMeta):
            """Sort key: (idle, cost). Both keys are NEGATIVE so largest-first
            sorts to the front via Python's default ascending sort:
            - idle = -last_executed (older → larger negative → first)
            - cost = -peak (higher → larger negative → first among same-idle)
            """
            idle = -float(candidate_meta.last_executed or 0.0)
            cost = 0.0
            if store is not None:
                inst = self.instances.get(candidate_meta.name)
                if inst is not None and inst.config_hash:
                    try:
                        rec = store.get_record(inst.instance_id, inst.config_hash)
                    except Exception:
                        rec = None
                    if rec is not None:
                        peak = rec.gpu_memory_mb_peak_max if needs_gpu else rec.memory_mb_peak_max
                        cost = -float(peak)
            return (idle, cost)
    
        candidates.sort(key=_eviction_priority)
    
        for candidate in candidates:
            self.logger.info(
                f"Evicting idle capability: {candidate.name} (Last used: {candidate.last_executed})"
            )
            if hasattr(candidate.instance, 'release'):
                candidate.instance.release()
            else:
                self.reload_capability(candidate.name)
            time.sleep(0.5) 
            if self.scheduler.allocate(needed_meta, self._get_global_stats):
                return True
            
        return False

    def execute_capability(
        self,
        name_or_id:str, # Capability name (default-loaded) or instance_id (multi-instance)
        *args,
        _task_name:Optional[str]=None, # CR-17 pt 2: route via the task channel (adapter task) instead of execute
        _method:Optional[str]=None, # CR-17 pt 2: adapter method (set with _task_name)
        **kwargs
    ) -> Any: # Capability result
        """Execute a capability instance's main functionality (sync).
    
        CR-10: resolves `name_or_id` via self.instances; per-instance enabled
        flag gates execution. `_running_executions` tracks by instance_id so
        concurrent multi-instance executes don't collide.
    
        CR-2: raises CapabilityDisabledError (typed) when the instance is disabled.
    
        CR-7: reactive retry on CapabilityResourceError — evicts other capabilities to
        free resources, then ALWAYS reloads the failing capability's worker before
        the retry attempt. Track A (WorkerOOMError — worker died from SIGKILL)
        needs the reload because there's no live worker to retry on. Track B
        (capability-raised CapabilityResourceError — worker still alive) ALSO reloads
        because PyTorch's CUDA caching allocator can fragment post-OOM in ways
        the capability can't clean up from within its own process; a fresh worker
        is the only reliable reset. Bounded by `self.max_retries` (default 1).
        Empirical sample recorded in the finally block — best-effort, doesn't
        break execute on failure.
        """
        inst = self.instances.get(name_or_id)
        if inst is None:
            raise ValueError(f"Capability/instance {name_or_id!r} not found or not loaded")
        if not inst.enabled:
            raise CapabilityDisabledError(inst.instance_id)
    
        instance_id = inst.instance_id  # stable across reload (preserved by reload_capability)
        capability_meta = self.capabilities.get(inst.capability_name)
    
        # CR-7 reactive retry loop. Defensive max_retries lookup so test fixtures
        # bypassing __init__ inherit the default behavior (one retry on resource).
        max_retries = getattr(self, 'max_retries', 1)
        last_resource_error: Optional[CapabilityResourceError] = None
        for attempt in range(max_retries + 1):
            if last_resource_error is not None and capability_meta is not None:
                # CR-6 Stage 4: notify substrate-side retry observer (best-effort).
                # JobQueue installs `self._on_retry` in `start()`; helper is invoked
                # with (instance_id, attempt_index, exception) so the queue can fire
                # RETRY_STARTED bound to the in-flight job + update Job.retry_count.
                _on_retry = getattr(self, '_on_retry', None)
                if _on_retry is not None:
                    try:
                        _on_retry(instance_id, attempt, last_resource_error)
                    except Exception:
                        pass  # Observer failure must not break the retry path
                self.logger.warning(
                    f"CR-7 reactive retry on {instance_id}: CapabilityResourceError "
                    f"(attempt {attempt+1}/{max_retries+1}); "
                    f"shortfall={getattr(last_resource_error, 'resource_shortfall', None)}; "
                    f"evicting + reloading + retrying"
                )
                self._reactive_evict_for(
                    capability_meta,
                    getattr(last_resource_error, 'resource_shortfall', None),
                    needy_instance_id=instance_id,
                )
                # CR-7: always reload — Track A (worker dead) needs it for any
                # retry to hit a live worker; Track B (capability-raised, worker alive)
                # also needs it because PyTorch's CUDA allocator can fragment
                # post-OOM. Fresh process is the only reliable allocator reset.
                # See SG-47 sub-task for Track B capability-side raise contract.
                saved_config = dict(inst.config) if inst is not None else None
                self.logger.info(
                    f"CR-7: reloading worker for {instance_id} after CapabilityResourceError "
                    f"({type(last_resource_error).__name__})"
                )
                self.reload_capability(instance_id, config=saved_config)
                inst = self.instances.get(instance_id)
                if inst is None:
                    self.logger.error(
                        f"CR-7: reload of {instance_id!r} failed; aborting retry"
                    )
                    raise last_resource_error
        
            # Existing pre-execute allocation + eviction flow (LRU + multi-axis under CR-7)
            inst.last_executed = time.time()
            if capability_meta is not None:
                capability_meta.last_executed = inst.last_executed
            stats_provider = self._get_global_stats
        
            if capability_meta is not None and not self.scheduler.allocate(capability_meta, stats_provider):
                self.logger.warning(f"Resources busy for {name_or_id}. Triggering eviction protocol.")
                if self._evict_for_resources(capability_meta):
                    self.logger.info("Eviction successful. Resources acquired.")
                else:
                    raise RuntimeError(
                        f"ResourceScheduler blocked execution of {name_or_id} (Eviction failed)"
                    )
        
            start_time = time.time()
            self._running_executions.add(inst.instance_id)
            self.scheduler.on_execution_start(inst.instance_id)
            success = False
            try:
                if _task_name is not None:
                    result = inst.proxy.execute_task(_task_name, _method, **kwargs)
                else:
                    result = inst.proxy.execute(*args, **kwargs)
                success = True
                return result
            except CapabilityResourceError as e:
                # Stash for the next iteration's retry-path; raise on the last attempt.
                if attempt < max_retries:
                    last_resource_error = e
                else:
                    raise
            finally:
                self.scheduler.on_execution_finish(inst.instance_id)
                self._running_executions.discard(inst.instance_id)
                self._maybe_fire_disable_hook(inst.instance_id)
                self._record_sample_safe(inst, start_time, success)

    async def execute_capability_async(
        self,
        name_or_id:str, # Capability name (default-loaded) or instance_id (multi-instance)
        *args,
        _task_name:Optional[str]=None, # CR-17 pt 2: route via the task channel (adapter task) instead of execute
        _method:Optional[str]=None, # CR-17 pt 2: adapter method (set with _task_name)
        **kwargs
    ) -> Any: # Capability result
        """Execute a capability instance's main functionality (async).
    
        CR-10 + CR-2: same semantics as execute_capability, async-flavored. Scheduler
        allocation goes through allocate_async for non-blocking polling.
    
        CR-7 + SG-33: reactive retry on CapabilityResourceError — always reloads
        before retry (Track A + Track B converge on the same reload path; see
        sync variant docstring for the rationale). Per-instance asyncio.Semaphore
        enforces the `max_concurrent_requests` cap (None = unbounded). Empirical
        sample recorded in the finally block.
        """
        inst = self.instances.get(name_or_id)
        if inst is None:
            raise ValueError(f"Capability/instance {name_or_id!r} not found or not loaded")
        if not inst.enabled:
            raise CapabilityDisabledError(inst.instance_id)
    
        instance_id = inst.instance_id
        capability_meta = self.capabilities.get(inst.capability_name)
    
        # SG-33 lazy semaphore (None when no cap configured for this instance).
        limiter = self._get_concurrent_limiter(instance_id)
    
        max_retries = getattr(self, 'max_retries', 1)
        last_resource_error: Optional[CapabilityResourceError] = None
        for attempt in range(max_retries + 1):
            if last_resource_error is not None and capability_meta is not None:
                # CR-6 Stage 4: notify substrate-side retry observer (best-effort).
                # JobQueue installs `self._on_retry` in `start()`; helper is invoked
                # with (instance_id, attempt_index, exception) so the queue can fire
                # RETRY_STARTED bound to the in-flight job + update Job.retry_count.
                _on_retry = getattr(self, '_on_retry', None)
                if _on_retry is not None:
                    try:
                        _on_retry(instance_id, attempt, last_resource_error)
                    except Exception:
                        pass  # Observer failure must not break the retry path
                self.logger.warning(
                    f"CR-7 reactive retry on {instance_id}: CapabilityResourceError "
                    f"(attempt {attempt+1}/{max_retries+1}); "
                    f"shortfall={getattr(last_resource_error, 'resource_shortfall', None)}; "
                    f"evicting + reloading + retrying"
                )
                self._reactive_evict_for(
                    capability_meta,
                    getattr(last_resource_error, 'resource_shortfall', None),
                    needy_instance_id=instance_id,
                )
                # CR-7: always reload — Track A worker-dead + Track B
                # allocator-fragmentation both demand a fresh process.
                saved_config = dict(inst.config) if inst is not None else None
                self.logger.info(
                    f"CR-7: reloading worker for {instance_id} after CapabilityResourceError "
                    f"({type(last_resource_error).__name__})"
                )
                self.reload_capability(instance_id, config=saved_config)
                inst = self.instances.get(instance_id)
                if inst is None:
                    self.logger.error(
                        f"CR-7: reload of {instance_id!r} failed; aborting retry"
                    )
                    raise last_resource_error
                # Reload may have swapped the limiter (different max_concurrent_requests
                # — though load_capability's reload-via-unload-then-load path passes None
                # here today; the lookup is correct in either case).
                limiter = self._get_concurrent_limiter(instance_id)
        
            inst.last_executed = time.time()
            if capability_meta is not None:
                capability_meta.last_executed = inst.last_executed
        
            if capability_meta is not None and not await self.scheduler.allocate_async(capability_meta, self._get_global_stats_async):
                self.logger.warning(f"Resources busy for {name_or_id}. Triggering eviction protocol.")
                if self._evict_for_resources(capability_meta):
                    self.logger.info("Eviction successful. Resources acquired.")
                else:
                    raise RuntimeError(
                        f"ResourceScheduler blocked execution of {name_or_id} (Eviction failed)"
                    )
        
            start_time = time.time()
            self._running_executions.add(inst.instance_id)
            self.scheduler.on_execution_start(inst.instance_id)
            success = False
            try:
                if limiter is not None:
                    # SG-33: gate concurrent executes behind the per-instance semaphore.
                    async with limiter:
                        if _task_name is not None:
                            result = await inst.proxy.execute_task_async(_task_name, _method, **kwargs)
                        else:
                            result = await inst.proxy.execute_async(*args, **kwargs)
                elif _task_name is not None:
                    result = await inst.proxy.execute_task_async(_task_name, _method, **kwargs)
                else:
                    result = await inst.proxy.execute_async(*args, **kwargs)
                success = True
                return result
            except CapabilityResourceError as e:
                if attempt < max_retries:
                    last_resource_error = e
                else:
                    raise
            finally:
                self.scheduler.on_execution_finish(inst.instance_id)
                self._running_executions.discard(inst.instance_id)
                self._maybe_fire_disable_hook(inst.instance_id)
                self._record_sample_safe(inst, start_time, success)

    def execute_capability_task(
        self,
        name_or_id:str, # Capability name (default-loaded) or instance_id (multi-instance)
        task_name:str, # Adapter task, e.g. "graph-storage"
        method:str, # Adapter method, e.g. "query_nodes"
        **kwargs
    ) -> Any: # Typed task result
        """CR-17 pt 2: execute a typed task-adapter method (explicit task channel; sync).

        Thin wrapper over `execute_capability` — the whole CR-7 retry / scheduler /
        empirical-sampling machinery applies identically to task-channel calls.
        """
        return self.execute_capability(name_or_id, _task_name=task_name, _method=method, **kwargs)

    async def execute_capability_task_async(
        self,
        name_or_id:str, # Capability name (default-loaded) or instance_id (multi-instance)
        task_name:str, # Adapter task, e.g. "graph-storage"
        method:str, # Adapter method, e.g. "query_nodes"
        **kwargs
    ) -> Any: # Typed task result
        """CR-17 pt 2: execute a typed task-adapter method (explicit task channel; async).

        Thin wrapper over `execute_capability_async` — CR-7 retry, SG-33 semaphore,
        admission and empirical sampling apply identically; this is the method
        the JobQueue's task-addressed jobs invoke.
        """
        return await self.execute_capability_async(
            name_or_id, _task_name=task_name, _method=method, **kwargs)

    def enable_capability(
        self,
        name_or_id:str # Capability name (default instance) or instance_id (multi-instance)
    ) -> bool: # True if instance was enabled
        """Enable a capability instance (CR-10 multi-instance aware).
    
        CR-2: persists the new state via `config_store` (default-instance only;
        persistence is per-capability, not per-instance) and fires the capability's
        on_enable hook on state-change. Idempotent for already-enabled instances.
        """
        inst = self.instances.get(name_or_id)
        if inst is None:
            return False
        was_disabled = not inst.enabled
        inst.enabled = True
        # Default instance: also sync the CapabilityMeta.enabled flag (backward compat)
        # and persist via config_store (per-capability persistence).
        if inst.instance_id == inst.capability_name:
            meta = self.capabilities.get(inst.capability_name)
            if meta is not None:
                meta.enabled = True
            self._persist_config(inst.capability_name)
        if was_disabled and inst.proxy is not None:
            try:
                inst.proxy.on_enable()
            except Exception as e:
                self.logger.warning(f"on_enable() raised for {name_or_id}: {e}")
        return True

    def disable_capability(
        self,
        name_or_id:str # Capability name (default instance) or instance_id (multi-instance)
    ) -> bool: # True if instance was disabled
        """Disable a capability instance without unloading it (CR-10 multi-instance aware).
    
        CR-2: persists the new state (default-instance only) and fires the
        capability's on_disable hook — but defers the hook until any in-flight job
        for THIS instance finishes (the per-instance `_running_executions` key
        is the instance_id, so a concurrent execute on a different instance of
        the same capability doesn't gate this instance's hook).
        """
        inst = self.instances.get(name_or_id)
        if inst is None:
            return False
        was_enabled = inst.enabled
        inst.enabled = False
        # Default instance: sync CapabilityMeta.enabled + persist
        if inst.instance_id == inst.capability_name:
            meta = self.capabilities.get(inst.capability_name)
            if meta is not None:
                meta.enabled = False
            self._persist_config(inst.capability_name)
        if was_enabled and inst.proxy is not None:
            if inst.instance_id in self._running_executions:
                self._pending_disable_hooks.add(inst.instance_id)
                self.logger.debug(
                    f"Deferring on_disable() for {inst.instance_id} until in-flight job finishes"
                )
            else:
                try:
                    inst.proxy.on_disable()
                except Exception as e:
                    self.logger.warning(f"on_disable() raised for {name_or_id}: {e}")
        return True

    def get_capability_diagnostics(
        self,
        name_or_id:str, # Capability name or instance_id
        limit:int=50, # Max records to return (most recent)
        include_stream:bool=True # Include raw stream chunks for the capability's worker sessions
    ) -> str: # Rendered diagnostic text (most recent last)
        """Render a capability's recent diagnostics as text (CR-14; replaces
        the retired flat-log accessor — the flat `.cjm/logs/*.log` files no longer exist).

        A convenience TEXT projection over the diagnostics store for operator /
        UI display: structured records (level + logger name + exact job id when
        stamped) merged with the raw stream chunks (prints / tqdm final frames /
        death rattles) from this capability's worker sessions, ordered by time.
        Programmatic consumers query the stores directly
        (`manager.diagnostics_store` / `JobQueue.get_job_diagnostics`).
        """
        inst = self.instances.get(name_or_id)
        capability_name = inst.capability_name if inst is not None else name_or_id

        # Worker sessions for this capability come from the journal (spawn events).
        sessions = []
        try:
            for ev in self.journal_store.query(event_type="worker_spawned",
                                               descending=True, limit=20):
                if ev.capability_name == capability_name and ev.worker_session_id:
                    sessions.append(ev.worker_session_id)
        except Exception as e:
            self.logger.warning(f"get_capability_diagnostics journal read failed: {e}")

        entries = []  # (ts, rendered line)
        try:
            for ws in sessions:
                for r in self.diagnostics_store.query_records(
                        worker_session_id=ws, limit=limit, descending=True):
                    job = f" job={r.job_id}" if r.job_id else ""
                    entries.append((r.ts, f"{r.ts.isoformat()} [{r.level}] {r.logger_name}{job} :: {r.message}"
                                    + (f"\n{r.exc_text}" if r.exc_text else "")))
                if include_stream:
                    for c in self.diagnostics_store.query_chunks(
                            worker_session_id=ws, limit=limit, descending=True):
                        entries.append((c.ts, f"{c.ts.isoformat()} [stream] {c.content}"))
        except Exception as e:
            return f"Error reading diagnostics: {e}"

        if not entries:
            return "No diagnostics found."
        entries.sort(key=lambda t: t[0])
        return "\n".join(line for _, line in entries[-limit:])

    def get_capability_config(
        self,
        capability_name: str # Name of the capability
    ) -> Optional[Dict[str, Any]]: # Current configuration or None
        """Get the current configuration of a capability."""
        capability = self.get_capability(capability_name)
        if capability:
            return capability.get_current_config()
        return None

    def get_capability_config_schema(
        self,
        capability_name: str # Name of the capability
    ) -> Optional[Dict[str, Any]]: # JSON Schema or None
        """Get the configuration JSON Schema for a capability."""
        capability = self.get_capability(capability_name)
        if capability:
            return capability.get_config_schema()
        return None

    def get_config_options(
        self,
        name_or_id: str # Capability name (default instance) or instance_id (multi-instance)
    ) -> Dict[str, Any]: # CR-11: live config option domains, or {} if unavailable
        """Get a capability instance's runtime config option providers (CR-11).
    
        Forwards to the worker's get_config_options() - live enum domains +
        per-option metadata for dynamic config fields (e.g. an API model list).
        Kept separate from get_capability_config_schema (static, hashed for CR-8 drift);
        these options are the live companion the capability-config UI merges on top.
    
        Degrades to {} if the instance is missing or the worker call fails - the UI
        then falls back to the static schema. Typed-error surfacing for the UI
        consumer is deferred to the capability-config UI library (Path C Step 4).
        """
        capability = self.get_capability(name_or_id)
        if capability is None:
            return {}
        try:
            return capability.config_options()
        except Exception as e:
            self.logger.warning(f"get_config_options({name_or_id!r}) failed: {e}")
            return {}

    def get_all_capability_configs(self) -> Dict[str, Dict[str, Any]]: # Capability name -> config mapping
        """Get current configuration for all loaded capabilities."""
        return {
            name: capability.get_current_config()
            for name, meta in self.capabilities.items()
            if meta.instance
            for capability in [meta.instance]
        }

    def update_capability_config(
        self,
        name_or_id: str, # Capability name (default instance) or instance_id (multi-instance)
        config: Dict[str, Any], # New configuration values
        strict: bool = True # SG-5: reject unknown keys against manifest config_schema (default)
    ) -> bool: # True if successful
        """Update a capability instance's configuration (CR-10 multi-instance aware).
    
        CR-2: on successful reconfigure, persists the new config (default instance
        only; multi-instance loads don't persist). Per-instance `inst.config` is
        updated regardless.
        SG-5: validates against the underlying capability's config_schema (per-capability,
        not per-instance, so all instances share the same schema).
        """
        inst = self.instances.get(name_or_id)
        if inst is None:
            self.logger.error(f"Capability/instance {name_or_id!r} not found")
            return False

        try:
            meta = self.capabilities.get(inst.capability_name)
            config_schema = (meta.manifest.get("config_schema") 
                            if (meta is not None and hasattr(meta, "manifest")) else None)
            validated_config = self._validate_config_against_schema(
                config, config_schema, inst.capability_name, strict=strict,
            )
            # CR-4 completion (2026-05-25): route through the reconfigure delta path
            # (old -> new) so the worker fires RELOAD_TRIGGER releases for changed
            # fields, then re-applies config. Fall back to initialize() only if the
            # worker predates /reconfigure (proxy.reconfigure returns False).
            old_config = dict(inst.config) if inst.config else {}
            if not inst.proxy.reconfigure(old_config, validated_config):
                inst.proxy.initialize(validated_config)
            inst.config = dict(validated_config)
            # CR-14 (stage-7 found bug, ledger K-entry): the hash MUST follow the
            # config — it was set only at load, so post-reconfigure empirical
            # samples were recorded under the OLD config's hash, polluting its
            # profile and defeating the stage-3 "config change = new hash =
            # auto-demotion" admission self-correction.
            inst.config_hash = compute_config_hash(dict(validated_config))
            self.logger.info(f"Updated configuration for instance: {inst.instance_id}")
            # CR-14: reconfigure is a journal event (the substrate boundary sees
            # it). Defensive getattr for __new__-style test fixtures.
            _journal = getattr(self, 'journal_store', None)
            if _journal is not None:
                _journal.append(JournalEvent(
                    event_type=SubstrateEventType.CONFIG_APPLIED.value,
                    capability_instance_id=inst.instance_id,
                    capability_name=inst.capability_name,
                    config_hash=inst.config_hash,
                    payload={"phase": "reconfigure",
                             "config_keys": sorted(validated_config or {})},
                ))
            # CR-2 + CR-10: persist only for the default instance (persistence is
            # per-capability, not per-instance).
            if inst.instance_id == inst.capability_name:
                self._persist_config(inst.capability_name)
            return True
        except CapabilityConfigError:
            raise
        except Exception as e:
            self.logger.error(f"Error updating {name_or_id!r} config: {e}")
            return False

    def reload_capability(
        self,
        name_or_id: str, # Capability name (default instance) or instance_id (multi-instance)
        config: Optional[Dict[str, Any]] = None # Optional new configuration
    ) -> bool: # True if successful
        """Reload a capability instance by terminating and restarting its Worker (CR-10)."""
        inst = self.instances.get(name_or_id)
        if inst is None:
            self.logger.error(f"Capability/instance {name_or_id!r} not found")
            return False
    
        capability_meta = self.capabilities.get(inst.capability_name)
        if capability_meta is None:
            self.logger.error(f"CapabilityMeta for {inst.capability_name!r} missing — cannot reload")
            return False
    
        try:
            # Capture current config if caller didn't supply one
            effective_config = config
            if effective_config is None and inst.proxy is not None:
                effective_config = inst.proxy.get_current_config()
        
            # Capture instance_id BEFORE unload (unload_capability removes the entry)
            target_instance_id = inst.instance_id
        
            self.unload_capability(target_instance_id)
            # Re-load using the same instance_id to preserve the addressing for callers
            return self.load_capability(
                capability_meta,
                effective_config,
                instance_id=target_instance_id,
            )
        except Exception as e:
            self.logger.error(f"Error reloading {name_or_id!r}: {e}")
            return False

    def get_capability_stats(
        self,
        name_or_id: str # Capability name (default instance) or instance_id (multi-instance)
    ) -> Optional[Dict[str, Any]]: # Resource telemetry or None
        """Get resource usage stats for a capability instance's Worker process (CR-10)."""
        inst = self.instances.get(name_or_id)
        if inst is not None and inst.proxy is not None and hasattr(inst.proxy, 'get_stats'):
            return inst.proxy.get_stats()
        return None

    async def execute_capability_stream(
        self,
        name_or_id: str,  # Capability name (default instance) or instance_id (multi-instance)
        *args,
        **kwargs
    ) -> AsyncGenerator[Any, None]:  # Async generator yielding results
        """Execute a capability instance with streaming response (CR-10 multi-instance aware).
    
        Same per-instance resolution as execute_capability_async; scheduler allocation
        keys off the CapabilityMeta (capability-level), execution + bookkeeping key off
        the CapabilityInstance (per-instance).
        """
        inst = self.instances.get(name_or_id)
        if inst is None:
            raise ValueError(f"Capability/instance {name_or_id!r} not found or not loaded")
        if not inst.enabled:
            raise ValueError(f"Capability/instance {name_or_id!r} is disabled")
    
        capability_meta = self.capabilities.get(inst.capability_name)
        if capability_meta is not None and not await self.scheduler.allocate_async(capability_meta, self._get_global_stats_async):
            raise RuntimeError(f"ResourceScheduler blocked execution of {name_or_id}")

        self.scheduler.on_execution_start(inst.instance_id)
        try:
            async for chunk in inst.proxy.execute_stream(*args, **kwargs):
                yield chunk
        finally:
            self.scheduler.on_execution_finish(inst.instance_id)

    async def load_capability_async(
        self,
        capability_meta: CapabilityMeta,
        config: Optional[Dict[str, Any]] = None,
        strict: bool = True,
        instance_id: Optional[str] = None,
        new_instance: bool = False,
    ) -> bool:
        """Async variant of `load_capability` (CR-10b).
    
        Runs the existing sync `load_capability` via `asyncio.to_thread` so the
        blocking proxy spawn + `_wait_for_ready` doesn't stall the event loop.
        Backward compat: identical behavior to the sync method, just non-blocking.
        """
        return await asyncio.to_thread(
            self.load_capability, capability_meta, config, strict, instance_id, new_instance,
        )

    async def unload_capability_async(
        self,
        name_or_id: str,
    ) -> bool:
        """Async variant of `unload_capability` (CR-10b)."""
        return await asyncio.to_thread(self.unload_capability, name_or_id)

    async def load_capabilities_concurrent(
        self,
        specs: List[CapabilityLoadSpec],  # Per-capability load specifications
        max_concurrency: Optional[int] = None,  # Cap simultaneous loads; None = unbounded
        fail_fast: bool = False,  # Re-raise first exception (default: collect all results)
    ) -> Dict[str, Union[str, Exception]]:  # requested_key → instance_id or Exception
        """CR-10b: fan out capability loads concurrently via asyncio.gather.
    
        Each spec is loaded via `load_capability_async` (`asyncio.to_thread` under the
        hood). The total wall-clock drops from sum-of-spawns to max-of-spawns when
        `max_concurrency=None`. Capped concurrency uses an asyncio.Semaphore.
    
        Result keys come from `_spec_requested_key`: explicit `instance_id` if set,
        `{capability_name}#new[{index}]` for ambiguous new_instance specs, else
        `capability_name`. Successful entries map to the resolved instance_id (string);
        failures map to the raised exception (caught regardless of fail_fast value
        for non-fail-fast mode; re-raised in fail_fast=True).
        """
        sem = asyncio.Semaphore(max_concurrency) if max_concurrency else None
    
        async def _load_one(spec: CapabilityLoadSpec) -> str:
            if sem:
                async with sem:
                    ok = await self.load_capability_async(
                        spec.meta, spec.config, True, spec.instance_id, spec.new_instance,
                    )
            else:
                ok = await self.load_capability_async(
                    spec.meta, spec.config, True, spec.instance_id, spec.new_instance,
                )
            if not ok:
                raise RuntimeError(
                    f"load_capability returned False for {spec.meta.name!r} "
                    f"(instance_id={spec.instance_id!r}, new_instance={spec.new_instance})"
                )
            # Resolve the actual instance_id from self.instances. For default/explicit
            # IDs we know it ahead of time; for new_instance=True it was auto-generated.
            if spec.instance_id is not None:
                return spec.instance_id
            if not spec.new_instance:
                return spec.meta.name
            # Auto-gen case: find the newest instance for this capability_name. Since
            # load_capability_async ran exclusively under the semaphore (or fully
            # concurrently if unbounded — but each spawn's generated ID is unique
            # by construction), we identify "the one just loaded" by created_at.
            candidates = [i for i in self.instances.values() if i.capability_name == spec.meta.name]
            if not candidates:
                raise RuntimeError(f"Could not resolve auto-gen instance_id for {spec.meta.name!r}")
            return max(candidates, key=lambda i: i.created_at).instance_id
    
        keys = [_spec_requested_key(spec, idx) for idx, spec in enumerate(specs)]
        tasks = [_load_one(spec) for spec in specs]
    
        if fail_fast:
            # gather without return_exceptions re-raises the first exception
            results = await asyncio.gather(*tasks)
        else:
            results = await asyncio.gather(*tasks, return_exceptions=True)
    
        return dict(zip(keys, results))

    async def unload_capabilities_concurrent(
        self,
        name_or_ids: List[str],  # Capability names or instance_ids to unload
        max_concurrency: Optional[int] = None,
        fail_fast: bool = False,
    ) -> Dict[str, Union[bool, Exception]]:  # name_or_id → True or Exception
        """CR-10b: fan out capability unloads concurrently via asyncio.gather.
    
        Same concurrency + fail_fast semantics as load_capabilities_concurrent. Result
        keys are the input `name_or_ids` (deduplication is the caller's
        responsibility; duplicate inputs produce one dict entry per unique key).
        """
        sem = asyncio.Semaphore(max_concurrency) if max_concurrency else None
    
        async def _unload_one(name_or_id: str) -> bool:
            if sem:
                async with sem:
                    return await self.unload_capability_async(name_or_id)
            return await self.unload_capability_async(name_or_id)
    
        tasks = [_unload_one(nid) for nid in name_or_ids]
    
        if fail_fast:
            results = await asyncio.gather(*tasks)
        else:
            results = await asyncio.gather(*tasks, return_exceptions=True)
    
        return dict(zip(name_or_ids, results))

    def bind(
        self,
        capability_name: str,  # Name of the capability to pre-bind
        default_config: Optional[Dict[str, Any]] = None  # Default config used by binding.load()
    ) -> "CapabilityBinding":  # Bound view ready for instance-style use
        """Create a CapabilityBinding pre-bound to this manager + capability_name."""
        return CapabilityBinding(
            manager=self,
            capability_name=capability_name,
            default_config=dict(default_config) if default_config else {},
        )

    def get_compatible_for_current_platform(self) -> List[CapabilityMeta]:  # Capabilities compatible with current platform
        """Phase 5a: return discovered capabilities compatible with the host platform.
    
        Filters by `resources.platforms`. Capabilities with an empty (or absent)
        platforms list are considered universally compatible — that's the
        introspection-time convention when a capability author didn't declare a
        platform constraint. Capabilities lacking the entire `resources` block
        (legacy / pre-Phase-5a manifests) also pass through as universal.
    
        Does NOT filter on `requires_gpu` — substrate doesn't know whether a
        GPU is present without invoking a system monitor capability. Callers gate
        on GPU availability separately if needed.
        """
        # Late import: platform module brings in subprocess + json; defer to call time.
        from cjm_substrate.core.platform import get_current_platform
        current = get_current_platform()
        out: List[CapabilityMeta] = []
        for m in self.discovered:
            if not m.resources or not m.resources.platforms:
                # No platform constraint declared; assume universal.
                out.append(m)
                continue
            if current in m.resources.platforms:
                out.append(m)
        return out


# Add to CapabilityManager


def _spec_requested_key(spec: CapabilityLoadSpec, index: int) -> str:
    """Derive the dict key the load_capabilities_concurrent result uses for `spec`.
    
    Resolution: explicit `instance_id` > `meta.name` + `#new[{index}]` suffix
    for ambiguous new_instance=True specs > `meta.name`. The suffix prevents
    key collision when multiple specs request a new instance of the same capability
    without explicit instance_ids.
    """
    if spec.instance_id is not None:
        return spec.instance_id
    if spec.new_instance:
        return f"{spec.meta.name}#new[{index}]"
    return spec.meta.name


@dataclass
class CapabilityBinding:
    """Pre-bound view of a single capability through a shared CapabilityManager.
    
    Eliminates the wrapper-class duplication audited across 8 consumer services
    (SG-17). Methods forward to the manager with `capability_name` pre-supplied;
    `default_config` is the fallback used when `load()` is called without an
    explicit config (matches the manifest-default behavior in `load_capability`).
    """
    manager: "CapabilityManager"  # The shared CapabilityManager
    capability_name: str  # Name of the capability this binding targets
    default_config: Dict[str, Any] = _field(default_factory=dict)  # Used when load() called without config
    
    # --- Observation ---
    
    @property
    def meta(self) -> Optional[CapabilityMeta]:
        """The CapabilityMeta if the capability is loaded, else None."""
        return self.manager.get_capability_meta(self.capability_name)
    
    @property
    def is_loaded(self) -> bool:
        """True if the capability is loaded in the bound manager."""
        return self.manager.get_capability(self.capability_name) is not None
    
    @property
    def is_enabled(self) -> bool:
        """True if the capability is loaded AND not currently disabled."""
        m = self.meta
        return m is not None and m.enabled
    
    # --- Lifecycle ---
    
    def load(
        self,
        config: Optional[Dict[str, Any]] = None,  # Override default_config when provided
        strict: bool = True  # SG-5 strict validation
    ) -> bool:  # True if loaded successfully
        """Load via the bound manager. Uses `default_config` if no `config` provided."""
        meta = self.manager.get_discovered_meta(self.capability_name)
        if meta is None:
            self.manager.logger.error(f"Capability {self.capability_name!r} not discovered")
            return False
        effective = config if config is not None else dict(self.default_config)
        return self.manager.load_capability(meta, effective, strict=strict)
    
    def unload(self) -> bool:  # True if unloaded
        """Unload the bound capability."""
        return self.manager.unload_capability(self.capability_name)
    
    def reload(
        self,
        config: Optional[Dict[str, Any]] = None  # Optional new config; current config used if None
    ) -> bool:
        """Reload the bound capability (terminate + restart worker)."""
        return self.manager.reload_capability(self.capability_name, config)
    
    def enable(self) -> bool:
        """Enable the bound capability."""
        return self.manager.enable_capability(self.capability_name)
    
    def disable(self) -> bool:
        """Disable the bound capability (worker stays alive; jobs rejected)."""
        return self.manager.disable_capability(self.capability_name)
    
    # --- Execution ---
    
    def execute(self, *args, **kwargs) -> Any:
        """Execute via the bound manager (sync)."""
        return self.manager.execute_capability(self.capability_name, *args, **kwargs)
    
    async def execute_async(self, *args, **kwargs) -> Any:
        """Execute via the bound manager (async)."""
        return await self.manager.execute_capability_async(self.capability_name, *args, **kwargs)
    
    # --- Configuration ---
    
    def update_config(
        self,
        config: Dict[str, Any],  # New config values
        strict: bool = True  # SG-5 strict validation
    ) -> bool:
        """Hot-reload the bound capability's configuration."""
        return self.manager.update_capability_config(self.capability_name, config, strict=strict)
    
    def get_config(self) -> Optional[Dict[str, Any]]:
        """Current configuration values (None if not loaded)."""
        return self.manager.get_capability_config(self.capability_name)
    
    def get_config_schema(self) -> Optional[Dict[str, Any]]:
        """JSON Schema describing this capability's configuration."""
        return self.manager.get_capability_config_schema(self.capability_name)
    
    def get_stats(self) -> Optional[Dict[str, Any]]:
        """Resource telemetry for the bound capability's worker process."""
        return self.manager.get_capability_stats(self.capability_name)
