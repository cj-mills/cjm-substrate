# cjm-substrate scripts

Operator tooling for the plugin substrate. Each script is standalone — call
the one you need; nothing chains automatically.

## `cascade_manifests.py` — CR-8 manifest regeneration cascade

Scans plugin manifests on disk and runs `cjm-ctl regenerate-manifest` on
plugins whose manifest needs a refresh. Two trigger conditions flag a
manifest for regeneration:

1. **Format upgrade** — manifest predates CR-8 (no `format_version` key).
   Regeneration upgrades the on-disk file from the legacy v1.0 flat layout
   to the nested v2.0 layout.
2. **Drift hash refresh** — v2.0 manifest already, but missing
   `drift_tracking.config_schema_hash` (e.g., upgraded via the v1.0 shim
   without ever being re-stamped). Regeneration recomputes the hash from a
   fresh live `/config_schema` response.

Dry-run by default. Idempotent — re-running on a clean ecosystem reports
"Nothing to do."

### Two scan modes

The substrate's intended architecture is **per-project `data_dir`**: each
project (page-centric library, host/orchestration library, well-maintained
plugin library) carries its own `cjm.yaml` declaring a project-local
`data_dir`. That keeps each end-application fully self-contained — its own
conda envs, its own manifests directory, no cross-project interference.

`~/.cjm/manifests/` is a fallback for bootstrap scenarios only.

**Single-project mode** (default) processes one substrate installation per
invocation:

```bash
# Uses cwd's cjm.yaml (resolves to that project's data_dir/manifests/)
cd /mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills/cjm-fasthtml-workflow-transcript-decomp
python scripts/cascade_manifests.py
python scripts/cascade_manifests.py --apply

# Override the scan dir directly (skips cjm.yaml resolution)
python scripts/cascade_manifests.py --manifests-dir /path/to/manifests
```

**Ecosystem mode** (`--scan-cjm-base PATH`) walks every `cjm-*/` directory
under `PATH`, parses each project's `cjm.yaml`, and cascades across the
union. Projects without `cjm.yaml` are listed and skipped — that's the seam
for plugin libraries that haven't yet migrated to per-project `data_dir`.
Mirrors `cascade_pins.py`'s ecosystem-walking pattern.

```bash
# Dry-run across every project under cj-mills/
python scripts/cascade_manifests.py \
    --scan-cjm-base /mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills

# Execute
python scripts/cascade_manifests.py \
    --scan-cjm-base /mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills \
    --apply
```

Per-project `--cjm-config <project>/cjm.yaml` is automatically passed to
`cjm-ctl regenerate-manifest` for each project so the introspection runs
against that project's runtime + writes to that project's manifests dir.

### Additional flags

```bash
# Use plugins.yaml as a fallback for legacy manifests missing the
# `package_source` field (rare; only pre-CR-1 plugins). Passed through
# to cjm-ctl regenerate-manifest --plugins.
python scripts/cascade_manifests.py --apply --plugins plugins.yaml

# Cheap format-only upgrade: load+write, no introspection. Doesn't
# refresh the code section or drift hash. Useful when plugin envs are
# unavailable (e.g., reading manifests on a machine without the plugin
# installed).
python scripts/cascade_manifests.py --apply --format-only
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0    | Nothing to do, or all upgrades succeeded |
| 1    | One or more regenerations failed |
| 2    | Configuration error (manifests dir missing, substrate not importable, mutually-exclusive flags) |

### Why this exists

CR-8 introduced the nested v2.0 manifest layout. The substrate reader handles
both v1.0 and v2.0 transparently via a `# REMOVE-AFTER-OVERHAUL` shim, but
plugins should migrate to v2.0 so the shim can retire in SG-48's cleanup
sweep. `cascade_manifests.py` does the bulk migration without needing each
plugin author to re-run `install-all` per project.

The cascade is also where new substrate-emitted fields (e.g., a future hash
algorithm change) get propagated across the ecosystem.

### Implementation notes

- The script imports `cjm_substrate.core.manifest_format` (for the
  `--format-only` path) and `cjm_substrate.core.config` (to resolve
  `manifests_dir` via `load_config(config_path=...)`). Run it from an
  environment where the substrate is installed.
- `load_config(config_path=...)` returns a fresh `CJMConfig` without
  mutating the module singleton — safe to call N times in a loop when
  scanning the ecosystem.
- `cjm-ctl regenerate-manifest` is invoked as a subprocess so each plugin's
  introspection runs in its own conda env. In ecosystem mode each
  invocation gets `--cjm-config <project>/cjm.yaml` so runtime + paths
  resolve correctly. If `cjm-ctl` isn't on `PATH`, the regenerate path
  fails — the script reports the failure and continues with the next plugin.
- Failures don't abort the cascade. The summary table at the end groups
  failures by project scope.
- **No topological sort** (unlike `cascade_pins.py`): manifests are
  independent — each plugin's regenerate output depends only on its own
  introspection script, not on any other plugin's manifest. Order doesn't
  matter.
