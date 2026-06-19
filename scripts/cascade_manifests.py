#!/usr/bin/env python3
"""
cascade_manifests.py

Ecosystem-wide manifest regeneration cascade per CR-8.

Two scan modes:

1. **Single-project** (default): scans the manifests directory resolved from
   the current working directory's `cjm.yaml` (or `~/.cjm/manifests/` as the
   bootstrap fallback when no `cjm.yaml` is present). One substrate
   installation per invocation.

2. **Ecosystem** (`--scan-cjm-base <PATH>`): walks every `cjm-*/` directory
   under `PATH`, parses each project's `cjm.yaml` to derive its per-project
   `manifests_dir`, and cascades across the union. Projects without
   `cjm.yaml` are skipped — that's the seam for plugin libraries that haven't
   yet migrated to per-project `data_dir`. Mirrors the ecosystem-walking
   pattern in `cascade_pins.py`.

For each manifest, two trigger conditions cause a flag:

1. **Format upgrade**: manifest predates CR-8 (no `format_version` key).
   Regeneration upgrades it to the nested v2.0 layout.
2. **Drift hash refresh**: v2.0 manifest already, but missing
   `drift_tracking.config_schema_hash`. Regeneration recomputes the hash.

Dry-run by default. Idempotent — re-running on a clean ecosystem reports
"Nothing to do."

USAGE
=====

    # Single-project (default — uses cwd's cjm.yaml or ~/.cjm/ fallback)
    cascade_manifests.py
    cascade_manifests.py --apply

    # Ecosystem walk (every cjm-*/ project with a cjm.yaml)
    cascade_manifests.py --scan-cjm-base /mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills
    cascade_manifests.py --scan-cjm-base /path/to/cj-mills --apply

    # Override the scan directory directly (skips cjm.yaml resolution)
    cascade_manifests.py --manifests-dir /path/to/manifests

    # Use plugins.yaml as the package_source fallback for legacy manifests
    cascade_manifests.py --apply --plugins plugins.yaml

    # Cheap format-only upgrade: load+write, no introspection. Doesn't
    # refresh the code section or drift hash. Useful when plugin envs
    # aren't available (e.g., reading manifests on a machine without
    # the plugin installed).
    cascade_manifests.py --apply --format-only

EXIT CODES
==========

    0  - Nothing to do (or all upgrades succeeded in --apply mode)
    1  - One or more regenerations failed
    2  - Configuration error (manifests dir missing, substrate not importable)
"""

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


CURRENT_FORMAT_VERSION = "2.0"


@dataclass
class ProjectScope:
    """One substrate-installation scope: a cjm.yaml + the manifests_dir it resolves to.

    `cjm_yaml` is None for the bootstrap fallback (no cjm.yaml found, scanning
    `~/.cjm/manifests/` directly). `project_root` is the parent of cjm.yaml,
    used as `--cjm-config` argument when invoking cjm-ctl per project.
    """
    label: str  # Display name (e.g., "cjm-fasthtml-workflow-transcript-decomp")
    cjm_yaml: Optional[Path]  # cjm.yaml path or None for fallback mode
    manifests_dir: Path  # Resolved manifests directory


def discover_single_project(
    manifests_dir_override: Optional[Path],
) -> List[ProjectScope]:
    """Single-project mode: resolve one ProjectScope from cwd or override.

    Resolution order:
      1. --manifests-dir explicit override (no cjm.yaml needed)
      2. ./cjm.yaml in cwd
      3. ~/.cjm/manifests/ bootstrap fallback
    """
    if manifests_dir_override:
        return [ProjectScope(
            label=f"explicit:{manifests_dir_override}",
            cjm_yaml=None,
            manifests_dir=manifests_dir_override,
        )]

    cwd_yaml = Path.cwd() / "cjm.yaml"
    if cwd_yaml.exists():
        try:
            from cjm_substrate.core.config import load_config
            cfg = load_config(config_path=cwd_yaml)
            return [ProjectScope(
                label=Path.cwd().name,
                cjm_yaml=cwd_yaml,
                manifests_dir=cfg.manifests_dir,
            )]
        except Exception as e:
            print(f"WARNING: failed to parse {cwd_yaml}: {e}", file=sys.stderr)

    # Bootstrap fallback: use the substrate default (~/.cjm/manifests/)
    try:
        from cjm_substrate.core.config import get_config
        cfg = get_config()
        return [ProjectScope(
            label="bootstrap (~/.cjm/)",
            cjm_yaml=None,
            manifests_dir=cfg.manifests_dir,
        )]
    except ImportError as e:
        print(f"ERROR: cjm_substrate not importable: {e}", file=sys.stderr)
        return []


def discover_ecosystem(
    base_path: Path,
) -> List[ProjectScope]:
    """Ecosystem mode: walk `base_path/cjm-*/` and collect per-project scopes.

    For each `cjm-*/` directory containing a `cjm.yaml`, parse the yaml and
    derive its manifests_dir. Projects without cjm.yaml are skipped (with a
    one-line note) — that's the seam for older plugin libraries that haven't
    migrated to per-project `data_dir` yet.

    Mirrors `cascade_pins.py`'s discovery pattern but produces scopes instead
    of touched-library entries.
    """
    if not base_path.exists():
        print(f"ERROR: base path {base_path} does not exist", file=sys.stderr)
        return []

    try:
        from cjm_substrate.core.config import load_config
    except ImportError as e:
        print(f"ERROR: cjm_substrate not importable: {e}", file=sys.stderr)
        return []

    scopes: List[ProjectScope] = []
    skipped_no_yaml: List[str] = []
    for project_dir in sorted(base_path.glob("cjm-*/")):
        if not project_dir.is_dir():
            continue
        yaml_path = project_dir / "cjm.yaml"
        if not yaml_path.exists():
            skipped_no_yaml.append(project_dir.name)
            continue
        try:
            cfg = load_config(config_path=yaml_path)
            scopes.append(ProjectScope(
                label=project_dir.name,
                cjm_yaml=yaml_path,
                manifests_dir=cfg.manifests_dir,
            ))
        except Exception as e:
            print(f"WARNING: failed to parse {yaml_path}: {e}", file=sys.stderr)

    if skipped_no_yaml:
        print(f"Skipped {len(skipped_no_yaml)} cjm-* directories without cjm.yaml "
              f"(per-project data_dir not yet adopted):")
        for name in skipped_no_yaml:
            print(f"  · {name}")
        print()

    return scopes


def classify_manifest(path: Path) -> Tuple[str, str]:
    """Return (status, reason) for a single manifest file.

    Status: 'current' / 'needs-format-upgrade' / 'needs-hash-refresh' / 'broken'.
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return ("broken", f"unreadable: {e}")
    if not isinstance(data, dict):
        return ("broken", f"non-object root: got {type(data).__name__}")

    fmt = data.get("format_version")
    if fmt is None:
        return ("needs-format-upgrade", "no format_version (v1.0 legacy flat)")
    if fmt != CURRENT_FORMAT_VERSION:
        return ("broken", f"unrecognized format_version {fmt!r}")

    # v2.0 — check drift hash presence
    hash_val = (data.get("drift_tracking") or {}).get("config_schema_hash")
    if not hash_val:
        return ("needs-hash-refresh", "v2.0 manifest missing drift_tracking.config_schema_hash")
    return ("current", f"v2.0 with hash {hash_val[:24]}...")


def format_only_upgrade(path: Path) -> Optional[str]:
    """Load via load_manifest (handles v1.0 shim) and re-write as v2.0.

    Returns None on success or an error message on failure. Does NOT run
    introspection — the code section keeps its on-disk content; only the
    layout flips from flat to nested.
    """
    try:
        from cjm_substrate.core.manifest_format import (
            load_manifest, write_manifest,
        )
    except ImportError as e:
        return f"cjm_substrate not importable: {e}"
    try:
        manifest = load_manifest(path)
        write_manifest(path, manifest)
        return None
    except Exception as e:
        return f"upgrade failed: {e}"


def run_regenerate(
    plugin_name: str,
    cjm_yaml: Optional[Path],
    plugins_yaml: Optional[Path],
) -> Tuple[int, str]:
    """Invoke `cjm-ctl regenerate-manifest <plugin_name>` as a subprocess.

    Passes `--cjm-config <cjm_yaml>` when set so cjm-ctl resolves the right
    project-scoped runtime + manifests_dir. Captures stdout+stderr so callers
    can render the failure context in the summary table.
    """
    cmd = ["cjm-ctl"]
    if cjm_yaml is not None:
        cmd.extend(["--cjm-config", str(cjm_yaml)])
    cmd.extend(["regenerate-manifest", plugin_name])
    if plugins_yaml:
        cmd.extend(["--plugins", str(plugins_yaml)])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    combined = (proc.stdout or "") + (proc.stderr or "")
    return (proc.returncode, combined.strip())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CR-8 ecosystem manifest regeneration cascade",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Execute the regenerations. Default is dry-run.",
    )
    parser.add_argument(
        "--manifests-dir", type=Path, default=None,
        help="Single-project mode: override manifests directory directly "
             "(skips cjm.yaml resolution).",
    )
    parser.add_argument(
        "--scan-cjm-base", type=Path, default=None,
        help="Ecosystem mode: walk <PATH>/cjm-*/ and cascade every project "
             "with a cjm.yaml. Mirrors cascade_pins.py's discovery pattern.",
    )
    parser.add_argument(
        "--plugins", type=Path, default=None,
        help="Path to plugins.yaml for package_source recovery on legacy "
             "manifests (passed through to cjm-ctl regenerate-manifest).",
    )
    parser.add_argument(
        "--format-only", action="store_true",
        help="Skip cjm-ctl regenerate. Just load+write to upgrade the layout "
             "without running introspection. Faster but doesn't refresh the "
             "code section or drift hash.",
    )
    args = parser.parse_args()

    # Mutually-exclusive guard: --manifests-dir + --scan-cjm-base
    if args.manifests_dir and args.scan_cjm_base:
        print("ERROR: --manifests-dir and --scan-cjm-base are mutually exclusive.",
              file=sys.stderr)
        return 2

    # Discover scopes
    if args.scan_cjm_base:
        scopes = discover_ecosystem(args.scan_cjm_base)
    else:
        scopes = discover_single_project(args.manifests_dir)

    if not scopes:
        return 2

    # Classify every manifest in every scope
    # Rows: (scope, plugin_name, status, reason, path)
    rows: List[Tuple[ProjectScope, str, str, str, Path]] = []
    empty_scopes: List[ProjectScope] = []
    for scope in scopes:
        if not scope.manifests_dir.exists():
            empty_scopes.append(scope)
            continue
        manifest_files = sorted(scope.manifests_dir.glob("*.json"))
        if not manifest_files:
            empty_scopes.append(scope)
            continue
        for path in manifest_files:
            status, reason = classify_manifest(path)
            plugin_name = path.stem
            rows.append((scope, plugin_name, status, reason, path))

    # Render header + per-scope plan
    print(f"=== Manifest Cascade Plan ===")
    print(f"  Mode: {'ecosystem walk' if args.scan_cjm_base else 'single project'}")
    print(f"  Scopes discovered: {len(scopes)}")
    print(f"  Scopes with manifests: {len(scopes) - len(empty_scopes)}")
    print(f"  Total manifests scanned: {len(rows)}")
    print()

    if empty_scopes:
        print(f"Scopes with no manifests on disk ({len(empty_scopes)}):")
        for scope in empty_scopes:
            print(f"  · {scope.label}: {scope.manifests_dir} (empty or absent)")
        print()

    # Counts
    counts: dict = {}
    for _, _, status, _, _ in rows:
        counts[status] = counts.get(status, 0) + 1
    print("Status breakdown:")
    for status in ("current", "needs-format-upgrade", "needs-hash-refresh", "broken"):
        if status in counts:
            print(f"  {status:<25} {counts[status]}")
    print()

    broken = [r for r in rows if r[2] == "broken"]
    todo = [r for r in rows if r[2] in ("needs-format-upgrade", "needs-hash-refresh")]

    if broken:
        print("Broken manifests (manual intervention needed):")
        for scope, name, _, reason, path in broken:
            print(f"  ✗ [{scope.label}] {name}: {reason}")
            print(f"      {path}")
        print()

    if not todo:
        if broken:
            return 1
        print("Nothing to do — all manifests are current.")
        return 0

    # Group todo by scope for the plan render
    print(f"Plugins flagged for regeneration ({len(todo)}):")
    last_scope_label: Optional[str] = None
    for scope, name, status, reason, _ in todo:
        if scope.label != last_scope_label:
            print(f"  ── {scope.label} ──")
            last_scope_label = scope.label
        marker = "↑" if status == "needs-format-upgrade" else "#"
        print(f"    {marker} {name}: {reason}")
    print()

    if not args.apply:
        print("Dry-run — pass --apply to execute.")
        return 0

    # Apply: run each regenerate (or format-only upgrade) per scope
    print(f"=== Applying ({'format-only' if args.format_only else 'regenerate'}) ===")
    failures: List[Tuple[ProjectScope, str, str]] = []
    successes: List[Tuple[ProjectScope, str]] = []
    last_scope_label = None
    for scope, name, _, _, path in todo:
        if scope.label != last_scope_label:
            print(f"\n── {scope.label} ──")
            last_scope_label = scope.label
        print(f"  → {name}")
        if args.format_only:
            err = format_only_upgrade(path)
            if err is None:
                print(f"    ✓ format upgraded")
                successes.append((scope, name))
            else:
                print(f"    ✗ {err}")
                failures.append((scope, name, err))
        else:
            rc, output = run_regenerate(name, scope.cjm_yaml, args.plugins)
            if rc == 0:
                print(f"    ✓ regenerated")
                if output:
                    for line in output.splitlines()[-3:]:
                        print(f"      {line}")
                successes.append((scope, name))
            else:
                print(f"    ✗ exit {rc}")
                for line in output.splitlines()[-5:]:
                    print(f"      {line}")
                last_line = output.splitlines()[-1] if output else "<no output>"
                failures.append((scope, name, f"exit {rc}: {last_line}"))

    # Summary
    print(f"\n=== Summary ===")
    print(f"  Succeeded: {len(successes)}")
    print(f"  Failed:    {len(failures)}")
    if failures:
        for scope, name, reason in failures:
            print(f"    [{scope.label}] {name}: {reason}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
