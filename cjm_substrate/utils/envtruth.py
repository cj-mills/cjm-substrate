"""Env truth for capability-served libs — the ONE sweep the six sightings demanded
(work item 424b9781, the craft register's env-truth family).

The manifest is the ONLY authority on which env serves a lib
(`install.python_path` — the 5th sighting's decoy rule), and EVERY workspace
mints its own `runtime/envs/<name>` worker envs sharing NAMES across
workspaces (the 6th sighting: a refreshed same-named env in another workspace
proves nothing). So after editing a capability-served lib the required ritual
is mechanical: enumerate every workspace whose manifests name the lib, resolve
each manifest's `install.python_path`, and read what that interpreter would
actually import. This module IS that ritual as a verb.

Truth is read from the env's dist-info directly (METADATA `Version` +
`direct_url.json` editable/origin — the same facts `pip show` derives),
never by executing the env's python: a sweep over N workspaces stays
subprocess-free. A workspace is any directory under the scan root carrying
`.cjm/manifests/`; the lib matches a manifest whose filename stem or
`code.name` equals it.

Doubles as the CLI: `python -m cjm_substrate.utils.envtruth <lib> [--root DIR]
[--json]` — and `cjm-ctl envs-for <lib>` wraps the same call. The closing
line prints the post-edit checklist verdict: every env either EDITABLE from
the named package_source (live immediately) or flagged as needing a refresh
(`pip install -e <source> --no-deps` with that env's python).
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _norm(name: str) -> str:
    """PEP 503-ish normalization to dist-info shape: lowercase, runs of -_. -> _."""
    return re.sub(r"[-_.]+", "_", name).lower()


def _read_dist_info(python_path: Path, dist_name: str) -> Optional[Dict[str, Any]]:
    """What `python_path` would import for `dist_name`: version + editable origin.

    Reads `<env>/lib/python3.*/site-packages/<name>-<ver>.dist-info` (METADATA
    `Version:`, `direct_url.json` `dir_info.editable` + `url`) — the facts
    `pip show` reports, without running the env."""
    env = python_path.resolve().parent.parent
    want = _norm(dist_name)
    for sp in sorted(env.glob("lib/python3.*/site-packages")):
        for di in sorted(sp.glob("*.dist-info")):
            stem = di.name[: -len(".dist-info")]
            name, _, version = stem.rpartition("-")
            if _norm(name) != want:
                continue
            meta_version = version
            meta = di / "METADATA"
            if meta.is_file():
                for line in meta.read_text(errors="replace").splitlines():
                    if line.startswith("Version:"):
                        meta_version = line.split(":", 1)[1].strip()
                        break
            editable, origin = False, ""
            du = di / "direct_url.json"
            if du.is_file():
                try:
                    d = json.loads(du.read_text())
                    editable = bool((d.get("dir_info") or {}).get("editable"))
                    origin = str(d.get("url") or "").removeprefix("file://")
                except (json.JSONDecodeError, OSError):
                    pass
            return {"version": meta_version, "editable": editable, "origin": origin,
                    "site_packages": str(sp)}
    return None


def _manifest_names_lib(path: Path, lib: str) -> Optional[Dict[str, Any]]:
    """The parsed manifest when it names `lib` (filename stem or code.name), else None."""
    if _norm(path.stem) != _norm(lib):
        try:
            probe = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        if _norm(str((probe.get("code") or {}).get("name") or "")) != _norm(lib):
            return None
        return probe
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def envs_for(
    lib: str,                       # The capability lib (dist name, e.g. cjm-capability-graph-sqlite)
    workspaces_root: Path,          # Directory whose children are scanned for <ws>/.cjm/manifests/
) -> List[Dict[str, Any]]:  # One row per (workspace, manifest) naming the lib
    """Every env the manifests say serves `lib`, with what that env actually holds."""
    rows: List[Dict[str, Any]] = []
    for mdir in sorted(workspaces_root.glob("*/.cjm/manifests")):
        ws = mdir.parent.parent
        for mf in sorted(mdir.glob("*.json")):
            manifest = _manifest_names_lib(mf, lib)
            if manifest is None:
                continue
            install = manifest.get("install") or {}
            code = manifest.get("code") or {}
            pp = str(install.get("python_path") or "")
            row: Dict[str, Any] = {
                "workspace": ws.name, "manifest": str(mf),
                "python_path": pp, "conda_env": str(install.get("conda_env") or ""),
                "package_source": str(install.get("package_source") or ""),
                "manifest_version": str(code.get("version") or ""),
            }
            if not pp:
                row["status"] = "NO python_path in manifest"
            elif not Path(pp).exists():
                row["status"] = "python_path MISSING on disk"
            else:
                truth = _read_dist_info(Path(pp), lib)
                if truth is None:
                    row["status"] = f"`{lib}` NOT INSTALLED in this env"
                else:
                    row.update(truth)
                    src = row["package_source"].rstrip("/")
                    if truth["editable"]:
                        same = (Path(truth["origin"]).resolve() == Path(src).resolve()
                                if truth["origin"] and src else False)
                        row["status"] = ("editable from package_source — edits are live "
                                         "on next process" if same else
                                         f"EDITABLE from elsewhere: {truth['origin']}")
                    else:
                        row["status"] = (f"NON-EDITABLE {truth['version']} — refresh with: "
                                         f"{pp} -m pip install -e {src or '<checkout>'} "
                                         "--no-deps")
            rows.append(row)
    return rows


def render_rows(lib: str, rows: List[Dict[str, Any]]) -> str:
    """The human sweep report + the post-edit checklist verdict line."""
    if not rows:
        return (f"no workspace manifest names `{lib}` under this root — "
                "wrong --root, or the lib is not capability-served")
    lines = [f"env truth for `{lib}` — {len(rows)} manifest(s):"]
    needs = 0
    for r in rows:
        ok = r.get("editable") and r["status"].startswith("editable from package_source")
        mark = "✓" if ok else "✗"
        if not ok:
            needs += 1
        ver = r.get("version") or r.get("manifest_version") or "?"
        lines.append(f"  {mark} [{r['workspace']}] {r['conda_env'] or r['python_path'] or '-'}"
                     f" · installed {ver} · {r['status']}")
    lines.append("post-edit checklist: " +
                 ("every serving env is editable from its package_source — nothing to do."
                  if needs == 0 else
                  f"{needs} env(s) will NOT see your edits — refresh each flagged env, "
                  "then relaunch its worker (fresh process)."))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Env truth sweep for a capability-served lib (manifest = the only authority)")
    ap.add_argument("lib", help="Dist name, e.g. cjm-capability-graph-sqlite")
    ap.add_argument("--root", type=Path, default=None,
                    help="Workspaces root to scan (default: parent of the enclosing "
                         "workspace, else the cwd's parent)")
    ap.add_argument("--json", action="store_true", help="Machine rows instead of the report")
    args = ap.parse_args(argv)
    root = args.root
    if root is None:
        here = Path.cwd()
        ws = next((p for p in [here, *here.parents] if (p / "cjm.yaml").is_file()), None)
        root = (ws.parent if ws is not None else here.parent)
    rows = envs_for(args.lib, root)
    if args.json:
        print(json.dumps({"lib": args.lib, "root": str(root), "rows": rows}, indent=2))
    else:
        print(f"root: {root}")
        print(render_rows(args.lib, rows))
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
