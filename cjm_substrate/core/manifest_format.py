"""Typed parser + writer for the nested v2.0 manifest layout (2026-05-19 substrate audit, CR-8).

Four sections mirror the JSON one-to-one: install (deployment-specific facts
populated at install time), code (code-derived facts refreshed by
`cjm-ctl regenerate-manifest`), drift_tracking (witness hashes recording the
shapes drift checks compare against), and overrides (an operator-supplied
overlay placeholder). Downstream code never sees a flat dict — only typed
sections; the legacy v1.0 flat reader shim was removed at SG-48, so
`load_manifest` fails loud on any format_version but "2.0"."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from cjm_substrate.core.metadata import ResourceRequirements
from cjm_substrate.utils.hashing import hash_bytes

CURRENT_FORMAT_VERSION = "2.0"  # Emitted on every freshly-written manifest


@dataclass
class InstallSection:
    """Deployment-specific facts populated at install time.
    
    These fields are written by `install_all` (paths, conda env, env vars)
    plus `_generate_manifest`'s post-introspection step (installed_at,
    installer_version, package_source). `regenerate-manifest` preserves
    the install section across regeneration so paths survive code-side
    refreshes.
    """
    python_path: str = ""        # Absolute path to the capability env's python interpreter
    conda_env: str = ""          # Conda environment name
    db_path: str = ""            # Capability's per-data SQLite path (if any)
    env_vars: Dict[str, str] = field(default_factory=dict)  # Per-capability env vars
    installed_at: str = ""       # ISO-8601 UTC timestamp of install/regen
    installer_version: str = ""  # "cjm-ctl <version>" that wrote this manifest
    package_source: str = ""     # Original install input (git URL or pip spec)


@dataclass
class CodeSection:
    """Code-derived facts refreshed by `cjm-ctl regenerate-manifest`.
    
    Everything in this section comes from running the introspection script
    inside the capability's conda env: metadata + config_schema + binary
    platform/hardware hard-facts. Drift detection hashes this section's
    `config_schema` field as its witness shape.
    
    `class_name` serializes as the JSON key `"class"` (Python reserved-word
    workaround).
    """
    name: str = ""               # Capability's unique identifier
    version: str = ""            # Capability's version string
    description: str = ""        # Brief description (SG-6 required)
    module: str = ""             # Importable module path for the capability class
    class_name: str = ""         # Capability class name (JSON key: "class")
    resources: Optional[ResourceRequirements] = None  # Phase 5a hard-facts
    config_schema: Optional[Dict[str, Any]] = None    # JSON Schema for capability config
    regenerated_at: Optional[str] = None              # ISO-8601 UTC of last regen
    worker_env: Optional[List[Dict[str, Any]]] = None # CR-12 spawn-env contract: asdict(EnvVarSpec) list
    structural_surface: Optional[Dict[str, Any]] = None  # Pass-2 Thread 3: public surface recorded in-env (methods/properties/attributes)


@dataclass
class DriftTracking:
    """Witness hashes for drift detection.
    
    `config_schema_hash` is computed at write time (regenerate-manifest /
    install_all) from a canonical JSON encoding of the code section's
    `config_schema`. The CapabilityManager's drift-check fetches the live
    `/config_schema` from the worker, hashes it the same way, and compares;
    a mismatch raises `CapabilityMeta.config_schema_drift = True` plus a
    warning log.
    """
    config_schema_hash: Optional[str] = None  # "sha256:hexdigest" of canonical config_schema
    structural_surface_hash: Optional[str] = None  # Pass-2 Thread 3 witness: hash of code.structural_surface (None = pre-surface manifest)


@dataclass
class ManifestV2:
    """Top-level v2.0 manifest with four named sections plus `format_version`.
    
    Loaded from a v2.0 nested JSON file as-is; `format_version` is always
    `CURRENT_FORMAT_VERSION`.
    """
    install: InstallSection = field(default_factory=InstallSection)
    code: CodeSection = field(default_factory=CodeSection)
    drift_tracking: DriftTracking = field(default_factory=DriftTracking)
    overrides: Dict[str, Any] = field(default_factory=dict)
    format_version: str = CURRENT_FORMAT_VERSION


def compute_config_schema_hash(
    schema: Optional[Dict[str, Any]],  # JSON Schema or None
) -> str:                              # "sha256:hexdigest"
    """Hash a JSON Schema with stable canonicalization.

    Canonical JSON (sorted keys, no whitespace) keeps the digest stable across
    Python versions and dict-insertion orders. Reuses
    `cjm_substrate.utils.hashing.hash_bytes` for the algo-tagged `"sha256:hex"`
    return shape the rest of the ecosystem already uses (graph capability,
    future bundle library).

    None is treated as `{}` — the hash records "no schema declared" rather
    than refusing. This way a capability that lost its config_schema between
    install and load still gets a drift warning rather than a crash.
    """
    canonical = json.dumps(schema or {}, sort_keys=True, separators=(",", ":"))
    return hash_bytes(canonical.encode("utf-8"))


def compute_structural_surface_hash(
    surface: Optional[Dict[str, Any]],  # derive_structural_surface output or None
) -> str:                               # "sha256:hexdigest"
    """Hash a structural surface with stable canonicalization.

    Same canonical-JSON + hash_bytes shape as `compute_config_schema_hash`
    (the CR-8 idiom). None hashes as `{}` — but note the drift check skips
    when the STORED hash is None (pre-surface-era manifest ≠ drift);
    `_generate_manifest` only writes a hash when a surface was recorded.
    """
    canonical = json.dumps(surface or {}, sort_keys=True, separators=(",", ":"))
    return hash_bytes(canonical.encode("utf-8"))


def _parse_resources_dict(d: Optional[Dict[str, Any]]) -> Optional[ResourceRequirements]:
    """Build a `ResourceRequirements` from its JSON sub-dict, or None."""
    if not d:
        return None
    return ResourceRequirements(
        requires_gpu=bool(d.get("requires_gpu", False)),
        platforms=list(d.get("platforms", []) or []),
        accelerators=list(d.get("accelerators", []) or []),
    )


def _from_v2_dict(
    data: Dict[str, Any],  # Parsed JSON dict with `format_version == "2.0"`
) -> ManifestV2:
    """Parse a v2.0 nested manifest dict into a typed `ManifestV2`."""
    install_d = data.get("install", {}) or {}
    code_d = data.get("code", {}) or {}
    drift_d = data.get("drift_tracking", {}) or {}
    install = InstallSection(
        python_path=install_d.get("python_path", "") or "",
        conda_env=install_d.get("conda_env", "") or "",
        db_path=install_d.get("db_path", "") or "",
        env_vars=dict(install_d.get("env_vars", {}) or {}),
        installed_at=install_d.get("installed_at", "") or "",
        installer_version=install_d.get("installer_version", "") or "",
        package_source=install_d.get("package_source", "") or "",
    )
    code = CodeSection(
        name=code_d.get("name", "") or "",
        version=code_d.get("version", "") or "",
        description=code_d.get("description", "") or "",
        module=code_d.get("module", "") or "",
        class_name=code_d.get("class", "") or "",
        resources=_parse_resources_dict(code_d.get("resources")),
        config_schema=code_d.get("config_schema"),
        regenerated_at=code_d.get("regenerated_at"),
        worker_env=code_d.get("worker_env"),
        structural_surface=code_d.get("structural_surface"),
    )
    drift = DriftTracking(
        config_schema_hash=drift_d.get("config_schema_hash"),
        structural_surface_hash=drift_d.get("structural_surface_hash"),
    )
    return ManifestV2(
        install=install,
        code=code,
        drift_tracking=drift,
        overrides=dict(data.get("overrides", {}) or {}),
        format_version=data.get("format_version", CURRENT_FORMAT_VERSION) or CURRENT_FORMAT_VERSION,
    )


def load_manifest(
    path: Union[str, Path],  # Path to manifest JSON file on disk
) -> ManifestV2:             # Parsed manifest in v2.0 typed shape
    """Load a manifest file and return a typed `ManifestV2`.
    
    Format detection by top-level `format_version` key:
    - `"2.0"` → nested layout, parse directly.
    - anything else (including missing) → ValueError (fail loud).
    """
    path = Path(path)
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"Manifest at {path} must be a JSON object, got {type(data).__name__}"
        )
    fmt = data.get("format_version")
    if fmt == CURRENT_FORMAT_VERSION:
        return _from_v2_dict(data)
    raise ValueError(
        f"Manifest at {path}: unrecognized format_version {fmt!r}. "
        f"Substrate supports {CURRENT_FORMAT_VERSION!r}."
    )


def _resources_to_dict(r: Optional[ResourceRequirements]) -> Optional[Dict[str, Any]]:
    """Serialize a `ResourceRequirements` back to its JSON sub-dict, or None."""
    if r is None:
        return None
    return {
        "requires_gpu": r.requires_gpu,
        "platforms": list(r.platforms),
        "accelerators": list(r.accelerators),
    }


def _code_section_to_dict(c: CodeSection) -> Dict[str, Any]:
    """Serialize a `CodeSection` to its JSON sub-dict, renaming `class_name` -> `class`."""
    d: Dict[str, Any] = {
        "name": c.name,
        "version": c.version,
        "description": c.description,
        "module": c.module,
        "class": c.class_name,
    }
    # Optional fields written only when populated, keeping manifests legible.
    if c.resources is not None:
        d["resources"] = _resources_to_dict(c.resources)
    if c.config_schema is not None:
        d["config_schema"] = c.config_schema
    if c.worker_env is not None:
        d["worker_env"] = c.worker_env
    if c.regenerated_at is not None:
        d["regenerated_at"] = c.regenerated_at
    if c.structural_surface is not None:
        d["structural_surface"] = c.structural_surface
    return d


def manifest_to_dict(
    m: ManifestV2,  # Manifest to serialize
) -> Dict[str, Any]:  # v2.0 nested dict ready for `json.dumps`
    """Serialize a `ManifestV2` to a v2.0 dict.

    Always emits `format_version == CURRENT_FORMAT_VERSION`. Exposed
    separately from `write_manifest` so callers that need the dict
    (`cjm-ctl validate`, tests) can pull it without going through disk.
    """
    return {
        "format_version": CURRENT_FORMAT_VERSION,
        "install": {
            "python_path": m.install.python_path,
            "conda_env": m.install.conda_env,
            "db_path": m.install.db_path,
            "env_vars": dict(m.install.env_vars),
            "installed_at": m.install.installed_at,
            "installer_version": m.install.installer_version,
            "package_source": m.install.package_source,
        },
        "code": _code_section_to_dict(m.code),
        "drift_tracking": {
            "config_schema_hash": m.drift_tracking.config_schema_hash,
            "structural_surface_hash": m.drift_tracking.structural_surface_hash,
        },
        "overrides": dict(m.overrides),
    }


def write_manifest(
    path: Union[str, Path],  # Output JSON file path
    manifest: ManifestV2,    # Manifest to serialize
) -> None:
    """Serialize a `ManifestV2` to disk in v2.0 nested layout (indent=2)."""
    path = Path(path)
    with open(path, "w") as f:
        json.dump(manifest_to_dict(manifest), f, indent=2)
