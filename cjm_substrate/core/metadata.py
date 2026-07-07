"""Data structures for capability metadata."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class ResourceRequirements:
    """Binary hard-facts about what a capability needs to run (Phase 5a).
    
    Quantitative resource amounts (min_vram_mb, etc.) deliberately omitted
    per CR-7's reactive resource management reframing — capability authors can't
    reliably estimate model × dtype × quantization combinatorics, and Blender-
    style variable-render capabilities can't estimate at all. The substrate uses
    these binary hard-facts purely for discovery filtering; actual resource
    contention is handled reactively by CR-7's eviction + retry flow.
    
    - `requires_gpu`: True if the capability needs any GPU; the substrate gates
      execution on a system monitor reporting one is present.
    - `platforms`: e.g., ["linux-x64", "darwin-arm64"]. Empty list means no
      platform constraint declared (assume universal compatibility).
    - `accelerators`: e.g., ["cuda", "mps", "cpu"]. Informational; substrate
      doesn't auto-select but consumers can filter on the values.
    """
    requires_gpu: bool = False
    platforms: List[str] = field(default_factory=list)
    accelerators: List[str] = field(default_factory=list)


@dataclass
class CapabilityMeta:
    """Metadata about a capability."""
    name:str # Capability's unique identifier
    version:str # Capability's version string
    description:str="" # Brief description of the capability's functionality
    # SG-35: `author` and `package_name` removed — author lives in pyproject.toml
    # + importlib.metadata; package_name is derivable from the import system.
    # `description` is retained and validated by SG-6's manifest checker.
    # Phase 5a: binary hard-facts for discovery filtering (no quantitative amounts
    # per CR-7 reactive reframing). Optional during the cascade window; None for
    # capabilities/legacy manifests that declare no resource constraints.
    resources:Optional["ResourceRequirements"]=None
    config_schema:Optional[Dict[str, Any]]=None # JSON Schema for capability configuration
    instance:Optional[Any]=None # Capability instance (ToolCapability subclass)
    enabled:bool=True # Whether the capability is enabled
    last_executed:float=0.0 # Unix timestamp
    # SG-9: drift detection — set by CapabilityManager.load_capability when the live
    # worker's /config_schema disagrees with the manifest's stored config_schema.
    # `live_config_schema` holds the worker-reported shape so callers can pick
    # which to honor (substrate keeps using `config_schema` for defaults +
    # validation; tooling and the future capability-config UI library can inspect
    # `live_config_schema` for the post-regenerate-manifest preview).
    config_schema_drift:bool=False
    live_config_schema:Optional[Dict[str, Any]]=None
    # Pass-2 Thread 3 (stage 2): set when the worker's live-derived
    # structural surface disagrees with the manifest's witness hash
    # (same CR-8 idiom as config_schema_drift; stage-4 adapter
    # compatibility matches against the RECORDED surface).
    structural_surface_drift:bool=False


@dataclass
class CapabilityInstance:
    """Per-instance runtime state for a loaded capability (CR-10 multi-instance).
    
    Differs from CapabilityMeta in scope:
    - CapabilityMeta is per-capability-name discovery + canonical-instance state.
    - CapabilityInstance is per-load-call runtime state.
    
    A capability loaded with no instance_id (default) gets `instance_id == capability_name`
    and is the canonical instance referenced by CapabilityMeta.instance. Multi-instance
    loads (instance_id != capability_name) add entries to CapabilityManager.instances
    without changing the canonical reference.

    Naming conventions (constrained-pattern, per the CR-10 Q-resolution):
    default `instance_id == capability_name` (backward-compat, the canonical
    instance); named ids are caller-passed constrained strings
    (`{alphanumeric, _, -}+`, len <= 64); auto-generated ids come from
    `new_instance=True` as `{capability_name}-{6-char hex}`. Enforcement
    lives in `CapabilityManager.load_capability`.
    """
    instance_id: str  # Unique key in CapabilityManager.instances; default = capability_name
    capability_name: str  # The underlying discovered capability's name (CapabilityMeta.name)
    config: Dict[str, Any] = field(default_factory=dict)  # Effective config used at initialize()
    # The actual proxy (RemoteCapabilityProxy) bound to this instance. Typed as Any
    # to avoid importing proxy.py here (proxy depends on interface; interface +
    # metadata stay decoupled per the dependency hierarchy).
    proxy: Optional[Any] = None
    enabled: bool = True  # Per-instance enable flag; substrate's execute_capability checks this
    last_executed: float = 0.0  # Unix timestamp of the most recent execute on this instance
    # Timezone-aware datetime — datetime.utcnow() is deprecated in Python 3.12+
    # per the CR-5 follow-up. The factory uses datetime.now(timezone.utc) at
    # instance-creation time.
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # CR-7: empirical resource tracking key. Populated by load_capability via
    # `compute_config_hash(config)` so per-execute sample recording can index
    # the EmpiricalResourceStore by (instance_id, config_hash). Two distinct
    # configs for the same instance get two distinct empirical records.
    # Defaults to empty string for back-compat with hand-constructed CapabilityInstances
    # in tests that don't go through load_capability.
    config_hash: str = ""
    # CR-7 / SG-33: per-instance concurrency cap for async execute. None means
    # unbounded (preserves pre-SG-33 behavior). When set, CapabilityManager creates
    # a lazy asyncio.Semaphore(max_concurrent_requests) keyed by instance_id and
    # gates execute_capability_async behind it. Sync execute_capability is NOT gated —
    # the cap is async-path only since sync callers can't await a semaphore.
    max_concurrent_requests: Optional[int] = None


@dataclass
class CapabilityLoadSpec:
    """One entry in `CapabilityManager.load_capabilities_concurrent`'s batch input (CR-10).
    
    Mirrors the positional arguments of `load_capability` so the concurrent helper
    can fan out load calls without repeating the per-spec instance_id /
    new_instance plumbing.
    
    - `meta`: the discovered CapabilityMeta to load (must have a `.manifest` attached).
    - `config`: initial configuration; falls through to persisted-or-schema-defaults
      when None (default-instance only; multi-instance starts fresh).
    - `instance_id`: explicit instance_id (validated against [A-Za-z0-9_-]{1,64}).
      None defaults to capability_name (single-instance backward compat).
    - `new_instance`: when True with instance_id=None, auto-generate
      `{capability_name}-{6-hex}`.
    """
    meta: Any  # CapabilityMeta — typed as Any to avoid forward-reference quirk under nbdev's late binding
    config: Optional[Dict[str, Any]] = None
    instance_id: Optional[str] = None
    new_instance: bool = False
