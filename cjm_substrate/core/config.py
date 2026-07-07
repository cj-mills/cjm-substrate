"""Project-level configuration for paths, runtime settings, and environment management."""

import os
import platform as platform_mod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

import yaml


class RuntimeMode(str, Enum):
    """Runtime mode for the capability system."""
    LOCAL = "local"    # Project-local runtime and data
    SYSTEM = "system"  # System-wide conda and ~/.cjm data


class CondaType(str, Enum):
    """Type of conda implementation to use."""
    MICROMAMBA = "micromamba"
    MINIFORGE = "miniforge"
    CONDA = "conda"


@dataclass
class RuntimeConfig:
    """Runtime environment configuration."""
    mode:RuntimeMode=RuntimeMode.SYSTEM # LOCAL for project-local, SYSTEM for global
    conda_type:CondaType=CondaType.CONDA # Conda implementation to use
    prefix:Optional[Path]=None # Path to runtime directory (LOCAL mode only)
    binaries:Dict[str, Path]=field(default_factory=dict) # Platform-specific binary paths


@dataclass
class SubstrateConfig:
    """Substrate behavior toggles.
    
    Loaded from the `substrate:` section of `cjm.yaml`. Each flag gates a
    substrate-wide behavior that hosts can disable when they don't want the
    per-load or per-execute cost.
    
    - `drift_detection` (CR-8): per-load `/config_schema` HTTP call + hash
      comparison against the manifest's stored hash. CapabilityManager's load
      path branches around `_check_config_schema_drift` when False.
    - `empirical_tracking` (CR-7): per-execute resource sample recording into
      `EmpiricalResourceStore`. CapabilityManager skips `record_sample` calls when
      False; the store's lazy-init also short-circuits.
    - `prefetch_stall_threshold_seconds` (CR-4 / Session A 2026-05-27): how long
      proxy.prefetch waits with no observed progress (via `/progress` polling)
      before declaring a stall. Replaces per-capability wall-clock timeouts —
      operators no longer race network speed against an arbitrary value. Capabilities
      defeat the stall counter by calling `self.report_progress(...)` periodically
      during long lifecycle operations (model download / vLLM server startup).
      Default 60 s; bump higher for capabilities that don't report progress, or lower
      if false-positive stalls are noisy.
    """
    drift_detection:bool=True # Run /config_schema hash compare on every load_capability
    empirical_tracking:bool=True # Record ResourceSample after every execute_capability*
    prefetch_stall_threshold_seconds:float=60.0 # CR-4 / Session A: stall detection threshold for proxy.prefetch
    diagnostics_retention_days:float=30.0 # CR-14 follow-up: age-based diagnostics retention; <=0 disables the startup sweep
    diagnostics_retention_max_mb:Optional[float]=None # CR-14 follow-up: diagnostics.db size budget (None = no size-based deletion)


@dataclass
class CJMConfig:
    """Main configuration for cjm-substrate."""
    runtime:RuntimeConfig=field(default_factory=RuntimeConfig) # Runtime environment settings
    data_dir:Path=field(default_factory=lambda: Path.home() / ".cjm") # Base directory for manifests, logs
    capabilities_config:Path=field(default_factory=lambda: Path("capabilities.yaml")) # Path to capabilities.yaml file
    models_dir:Optional[Path]=None # Directory for model downloads
    substrate:SubstrateConfig=field(default_factory=SubstrateConfig) # CR-8 substrate behavior toggles

    @property
    def manifests_dir(self) -> Path: # Directory containing capability manifests
        """Directory containing capability manifests."""
        return self.data_dir / "manifests"

    @property
    def capability_data_dir(self) -> Path: # Directory for capability runtime data
        """Directory for capability runtime data (databases, caches)."""
        return self.data_dir / "data"

    @property
    def journal_db_path(self) -> Path: # Journal store (CR-14: durable account-of-action)
        """Journal store path — the precious, host-written observability record."""
        return self.data_dir / "journal.db"

    @property
    def diagnostics_db_path(self) -> Path: # Diagnostics store (CR-14: disposable narrative)
        """Diagnostics store path — worker records + raw stream chunks; retention-managed."""
        return self.data_dir / "diagnostics.db"

    @property
    def conda_binary_path(self) -> Optional[Path]: # Path to conda/micromamba binary or None
        """Get the configured binary path for the current platform."""
        # Inline platform detection to avoid circular imports
        system = platform_mod.system().lower()
        machine = platform_mod.machine().lower()
        
        if system == "windows":
            system = "win"
        if machine in ("x86_64", "amd64"):
            arch = "x64"
        elif machine in ("arm64", "aarch64"):
            arch = "arm64"
        else:
            arch = machine
        
        platform_key = f"{system}-{arch}"
        
        if self.runtime.binaries and platform_key in self.runtime.binaries:
            return self.runtime.binaries[platform_key]
        
        # Default location if prefix is set
        if self.runtime.prefix:
            binary_name = "micromamba.exe" if system == "win" else "micromamba"
            return self.runtime.prefix / "bin" / binary_name
        
        return None


# Module-level configuration singleton
_current_config: Optional[CJMConfig] = None


def _load_from_yaml(
    yaml_path:Path # Path to cjm.yaml file
) -> CJMConfig: # Parsed configuration
    """Load config from YAML file, resolving relative paths."""
    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}

    # Resolve relative paths against yaml file location
    base_dir = yaml_path.parent.resolve()

    # Parse runtime config
    runtime_data = data.get("runtime", {})
    runtime = RuntimeConfig(
        mode=RuntimeMode(runtime_data.get("mode", "system")),
        conda_type=CondaType(runtime_data.get("conda_type", "conda")),
        prefix=base_dir / runtime_data["prefix"] if runtime_data.get("prefix") else None,
        binaries={k: base_dir / v for k, v in runtime_data.get("binaries", {}).items()}
    )

    # CR-8 + CR-7: parse substrate behavior toggles. Unknown keys ignored
    # (forward-compat for future flags landing without breaking older yamls).
    substrate_data = data.get("substrate", {}) or {}
    substrate = SubstrateConfig(
        drift_detection=bool(substrate_data.get("drift_detection", True)),
        empirical_tracking=bool(substrate_data.get("empirical_tracking", True)),
        prefetch_stall_threshold_seconds=float(substrate_data.get(
            "prefetch_stall_threshold_seconds", 60.0
        )),
        # CR-14 follow-up: diagnostics retention policy (journal is NEVER
        # auto-deleted — only the disposable class has a policy at all).
        diagnostics_retention_days=float(substrate_data.get(
            "diagnostics_retention_days", 30.0
        )),
        diagnostics_retention_max_mb=(
            float(substrate_data["diagnostics_retention_max_mb"])
            if substrate_data.get("diagnostics_retention_max_mb") is not None
            else None
        ),
    )

    # Parse top-level config
    config = CJMConfig(runtime=runtime, substrate=substrate)

    if "data_dir" in data:
        config.data_dir = base_dir / data["data_dir"]
    if "capabilities_config" in data:
        config.capabilities_config = base_dir / data["capabilities_config"]
    if "models_dir" in data:
        config.models_dir = base_dir / data["models_dir"]

    return config


def load_config(
    config_path:Optional[Path]=None, # CLI --cjm-config
    data_dir:Optional[Path]=None, # CLI --data-dir
    conda_prefix:Optional[Path]=None, # CLI --conda-prefix
    conda_type:Optional[str]=None # CLI --conda-type
) -> CJMConfig: # Resolved configuration
    """Load config with layered resolution (CLI > env vars > yaml > defaults)."""
    # 1. Start with defaults
    config = CJMConfig()

    # 2. Load cjm.yaml: specified path, else walk UP from CWD to the first
    #    cjm.yaml (schema v2 — install-all/setup-host run flagless from anywhere
    #    in the project tree; capabilities_config then resolves relative to it).
    yaml_path = config_path
    if yaml_path is None:
        for _d in [Path.cwd(), *Path.cwd().parents]:
            _cand = _d / "cjm.yaml"
            if _cand.exists():
                yaml_path = _cand
                break
    if yaml_path is not None and yaml_path.exists():
        config = _load_from_yaml(yaml_path)

    # 3. Override with environment variables
    # NOTE (T31): `CJM_DATA_DIR` here is the OPERATOR-facing knob for the
    # substrate ROOT (`cfg.data_dir` — the parent of manifests/ data/ logs/
    # secrets/). It is DISTINCT from the worker-injected `CJM_CAPABILITY_DATA_DIR`
    # (= `cfg.capability_data_dir` = `<data_dir>/data`), which the substrate sets on
    # each worker subprocess. Renaming the injection var (proxy/manager/cli)
    # removed the prior overload where one name meant two different paths; this
    # root knob deliberately keeps the `CJM_DATA_DIR` name.
    if env_data_dir := os.environ.get("CJM_DATA_DIR"):
        config.data_dir = Path(env_data_dir)
    if env_conda_prefix := os.environ.get("CJM_CONDA_PREFIX"):
        config.runtime.prefix = Path(env_conda_prefix)
    if env_conda_type := os.environ.get("CJM_CONDA_TYPE"):
        config.runtime.conda_type = CondaType(env_conda_type)

    # 4. Override with CLI args (highest priority)
    if data_dir:
        config.data_dir = data_dir
    if conda_prefix:
        config.runtime.prefix = conda_prefix
    if conda_type:
        config.runtime.conda_type = CondaType(conda_type)

    return config


def get_config() -> CJMConfig: # Current configuration
    """Get current config (loads defaults if not set)."""
    global _current_config
    if _current_config is None:
        _current_config = load_config()
    return _current_config


def set_config(
    config:CJMConfig # Configuration to set as current
) -> None:
    """Set current config (called by CLI callback)."""
    global _current_config
    _current_config = config


def reset_config() -> None:
    """Reset to unloaded state (for testing)."""
    global _current_config
    _current_config = None
