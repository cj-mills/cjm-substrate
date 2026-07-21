"""Workspace resolution: the marker-rooted directory that owns a pipeline's local artifacts (runs, graph data, substrate stores, TUI sidecars)."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

# The visible marker file that declares a directory as a workspace root.
# Distinct from `cjm.yaml` (a substrate project/install config): a workspace
# marks a pipeline-DATA locus, which may live in a media directory with no
# substrate project at all.
WORKSPACE_MARKER = "cjm-workspace.yaml"

# Environment override consulted between the explicit flag and the upward walk.
WORKSPACE_ENV_VAR = "CJM_WORKSPACE"


def _load_marker(
    root: Path  # Workspace root (the directory containing the marker)
) -> Dict[str, Any]:  # Parsed marker mapping; {} for empty/unreadable/non-mapping
    """Parse the marker YAML defensively — a bad marker degrades to defaults, never raises."""
    marker = root / WORKSPACE_MARKER
    try:
        data = yaml.safe_load(marker.read_text())
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


class WorkspaceError(Exception):
    """A workspace was named (flag or env) but could not be resolved.

    Raised only for EXPLICIT references — a marker miss on the upward-walk
    path is a normal outcome (`resolve_workspace` returns None) so hosts keep
    their pre-workspace cwd-relative behavior."""
    pass


@dataclass(frozen=True)
class Workspace:
    """A resolved workspace: the marker-rooted directory owning pipeline artifacts.

    Derived paths are conventions, not guarantees — `ensure_layout()` creates
    them. Manifest WRITERS record paths workspace-relative via `relative()`;
    READERS resolve recorded paths via `resolve_recorded()`. Recorded paths stay
    the source of truth — the workspace supplies defaults for NEW writes only.
    """
    root: Path  # Absolute workspace root (the directory containing the marker)

    @property
    def marker_path(self) -> Path:  # The workspace's declaring marker file
        """Path of the marker file that declares this root."""
        return self.root / WORKSPACE_MARKER

    @property
    def runs_dir(self) -> Path:  # Shared run-manifest dir for all workflow cores
        """Run-manifest dir shared by the workflow cores (format tags keep cohabitation safe)."""
        return self.root / "runs"

    @property
    def substrate_data_dir(self) -> Path:  # Feeds CJMConfig.data_dir at host wiring time
        """Substrate store root: manifests/, data/, journal.db etc. land under here."""
        return self.root / ".cjm"

    @property
    def sidecars_dir(self) -> Path:  # Per-workspace TUI settings + sidecar state
        """Sidecar-state dir for TUI settings and other per-workspace host state."""
        return self.root / ".cjm" / "sidecars"

    @property
    def name(self) -> str:  # Display name: marker `name:` field, else the root dir name
        """Human-facing workspace name."""
        marker_name = _load_marker(self.root).get("name")
        return str(marker_name) if marker_name else self.root.name

    def ensure_layout(self) -> None:
        """Create the conventional dirs (idempotent)."""
        for d in (self.runs_dir, self.substrate_data_dir, self.sidecars_dir):
            d.mkdir(parents=True, exist_ok=True)

    def relative(
        self,
        path: Path  # Path to record (absolute or cwd-relative)
    ) -> str:  # Workspace-relative POSIX string, or the absolute path when outside the root
        """Workspace-relative form for RECORDING a path in a manifest.

        Paths outside the root stay absolute — relocatability applies only to
        artifacts the workspace owns."""
        p = Path(path).resolve()
        try:
            return p.relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return str(p)

    def resolve_recorded(
        self,
        recorded: str  # A manifest-recorded path (workspace-relative or absolute)
    ) -> Path:  # Concrete path: relative forms anchor at the root, absolute pass through
        """Resolve a manifest-recorded path against this workspace."""
        p = Path(recorded)
        return p if p.is_absolute() else self.root / p


def find_workspace_root(
    start: Optional[Path] = None  # Walk origin; None = Path.cwd()
) -> Optional[Path]:  # First ancestor (including start) containing the marker, or None
    """Upward walk — the git-style discovery that makes workspace identity launch-cwd-independent."""
    d = (start or Path.cwd()).resolve()
    for candidate in [d, *d.parents]:
        if (candidate / WORKSPACE_MARKER).is_file():
            return candidate
    return None


def resolve_workspace(
    explicit: Optional[Path] = None,  # --workspace flag value (must be a marker-bearing root)
    cwd: Optional[Path] = None,  # Upward-walk origin; None = Path.cwd()
    env: Optional[Mapping[str, str]] = None  # Environment mapping; None = os.environ
) -> Optional[Workspace]:  # Resolved workspace, or None (host keeps its pre-workspace defaults)
    """Resolve the active workspace: explicit flag > CJM_WORKSPACE env > upward walk > None.

    Explicit references (flag/env) to a directory WITHOUT a marker raise
    WorkspaceError — loud, never a silent fallback; declare the root with
    `init_workspace` first. A quiet walk miss returns None so hosts fall back
    to their legacy cwd-relative behavior."""
    if explicit is not None:
        root = Path(explicit).expanduser().resolve()
        if not (root / WORKSPACE_MARKER).is_file():
            raise WorkspaceError(
                f"--workspace {root} has no {WORKSPACE_MARKER} — not a workspace root "
                f"(declare one with init_workspace / the workspace init verb)"
            )
        return Workspace(root=root)
    env_map = os.environ if env is None else env
    env_val = env_map.get(WORKSPACE_ENV_VAR)
    if env_val:
        root = Path(env_val).expanduser().resolve()
        if not (root / WORKSPACE_MARKER).is_file():
            raise WorkspaceError(
                f"{WORKSPACE_ENV_VAR}={env_val} has no {WORKSPACE_MARKER} — not a workspace root"
            )
        return Workspace(root=root)
    found = find_workspace_root(cwd)
    return Workspace(root=found) if found is not None else None


def init_workspace(
    root: Path,  # Directory to declare as a workspace root (created if missing)
    name: Optional[str] = None  # Optional display name recorded in a fresh marker
) -> Workspace:  # The (possibly pre-existing) workspace, layout ensured
    """Declare a workspace: write the marker + create the conventional layout.

    Idempotent: an existing marker is left untouched (`name` is not rewritten)."""
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    ws = Workspace(root=root)
    if not ws.marker_path.is_file():
        payload: Dict[str, Any] = {"name": name or root.name}
        ws.marker_path.write_text(yaml.safe_dump(payload, sort_keys=False))
    ws.ensure_layout()
    return ws


def workspace_doctor(
    ws: Workspace  # Workspace to check
) -> List[str]:  # Human-readable check lines, each prefixed "ok:" or "warn:"
    """Integrity-check skeleton for the workspace doctor verb.

    v1 owns structural checks only; deeper rungs (the demand-gated audio-stack
    doctor, 1e729301) mount here when deployment demands them."""
    lines: List[str] = []
    if ws.marker_path.is_file():
        lines.append(f"ok: marker {ws.marker_path} (name: {ws.name})")
    else:
        lines.append(f"warn: marker missing at {ws.marker_path}")
    for label, d in (
        ("runs dir", ws.runs_dir),
        ("substrate data dir", ws.substrate_data_dir),
        ("sidecars dir", ws.sidecars_dir),
    ):
        if not d.is_dir():
            lines.append(f"warn: {label} {d} missing (ensure_layout creates it)")
        elif not os.access(d, os.W_OK):
            lines.append(f"warn: {label} {d} not writable")
        else:
            lines.append(f"ok: {label} {d}")
    return lines


# Token marking a workspace-relative recorded path in serialized artifacts
# (run manifests etc.). The explicit prefix keeps the reader walk safe and
# generic — echoes the worker-env ${...} template convention.
WS_TOKEN = "${WS}"


def relativize_recorded(
    data: Any,  # JSON-serializable tree (dicts/lists/str scalars)
    ws: Optional[Workspace]  # Active workspace; None = record unchanged (legacy absolute)
) -> Any:  # Deep copy with workspace-owned absolute paths rewritten to "${WS}/<rel>"
    """Writer half of the 5daadfc4 recording contract (rung f).

    Walks a serialized manifest and rewrites every string value that is an
    absolute path under the workspace root to the token form
    "${WS}/<posix-rel>", making the artifact relocatable with the workspace.
    Paths OUTSIDE the root — and everything that isn't such a path — pass
    through untouched, so media libraries elsewhere on disk stay absolute."""
    if ws is None:
        return data
    root = str(ws.root.resolve())
    prefix = root.rstrip("/") + "/"

    def walk(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, str):
            if v == root:
                return WS_TOKEN
            if v.startswith(prefix):
                return f"{WS_TOKEN}/{Path(v).relative_to(root).as_posix()}"
        return v

    return walk(data)


def resolve_recorded_tree(
    data: Any,  # Parsed manifest tree (dicts/lists/str scalars)
    manifest_path: Path  # Where the manifest was LOADED from (the anchor source)
) -> Any:  # Deep copy with "${WS}/..." strings resolved to absolute path strings
    """Reader half of the recording contract (rung f; anchor rule ratified 2026-07-19).

    "${WS}/<rel>" resolves against, in order: (1) the manifest's own location —
    its dir's PARENT, since manifests conventionally live in <root>/runs/ — so
    a copied or moved workspace reads correctly with NO workspace resolved;
    (2) the ACTIVE workspace root, when the location-derived candidate does not
    exist on disk. Absolute (legacy) recorded paths pass through untouched.
    Downstream code keeps seeing absolute paths — only load seams call this."""
    primary = Path(manifest_path).resolve().parent.parent
    try:
        ws = resolve_workspace()
    except WorkspaceError:
        ws = None
    token_prefix = WS_TOKEN + "/"

    def resolve_one(v: str) -> str:
        rel = v[len(token_prefix):]
        cand = primary / rel
        if cand.exists():
            return str(cand)
        if ws is not None and (ws.root / rel).exists():
            return str(ws.root / rel)
        return str(cand)

    def walk(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        if isinstance(x, str):
            if x == WS_TOKEN:
                return str(primary)
            if x.startswith(token_prefix):
                return resolve_one(x)
        return x

    return walk(data)
