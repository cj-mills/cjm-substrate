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

Doubles as the CLI: `python -m cjm_substrate.utils.envtruth [<lib>] [--root DIR]
[--mode dev|distribution] [--json] [--strict]` — and `cjm-ctl envs-for` wraps
the same call. MODE-AWARE since DEC f282571c (the refresh verb): a manifest
matches when it names the lib OR its env holds it (finding 74ae00f5 — the
substrate is in every worker env yet named by none), the row carries the
env's WHOLE cjm-* set, and the verdict is per mode — dev: the capability
editable from package_source and every cjm-* dist editable; distribution:
every cjm-* dist a release whose version == the manifest's recorded pin
(`install.resolved`). Both flag a substrate gap against the host — the gap
the launch-time check refuses. Flagged rows name their recipe:
`cjm-ctl refresh`, then relaunch running workers.
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


def source_version(checkout: Path) -> Optional[str]:  # the version a checkout's SOURCE carries, or None
    """The version an editable install really runs: a static `project.version`
    in the checkout's pyproject.toml, else the setuptools dynamic `attr`
    read off the package's `__version__` line. Installed metadata lags a bump
    until the next `pip install -e` (finding d8cc4a2a: three recorded kit
    versions over one source tree) — for an editable dist the source is the
    truth, so `_iter_dist_infos` reads through to it."""
    pyproject = checkout / "pyproject.toml"
    if not pyproject.is_file():
        return None
    import tomllib
    try:
        data = tomllib.loads(pyproject.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return None
    version = str((data.get("project") or {}).get("version") or "")
    if version:
        return version
    attr = str((((data.get("tool") or {}).get("setuptools") or {}).get("dynamic") or {})
               .get("version", {}).get("attr") or "")
    if not attr:
        return None
    mod = attr.rsplit(".", 1)[0].replace(".", "/")
    for cand in (checkout / mod / "__init__.py", checkout / f"{mod}.py"):
        if cand.is_file():
            m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', cand.read_text(), re.M)
            return m.group(1) if m else None
    return None


def _iter_dist_infos(python_path: Path):  # yields (dist name, truth dict) per installed dist
    """Every `*.dist-info` the interpreter at `python_path` would import, as
    `(name, {version, metadata_version, editable, origin, site_packages})` —
    the `pip list` facts read straight off disk (no subprocess). Walks the
    POSIX layout (`lib/python3.*/site-packages`) and the Windows one
    (`Lib/site-packages`); the dist NAME comes from METADATA `Name:` when
    present (the dist-info stem normalizes `-` to `_`), else from the stem.
    For an EDITABLE dist `version` reads through to the checkout's source
    (`source_version`) — installed metadata lags a bump until the next
    `pip install -e` — and `metadata_version` keeps what pip recorded."""
    env = python_path.resolve().parent.parent
    sps = sorted(env.glob("lib/python3.*/site-packages")) + sorted(env.glob("Lib/site-packages"))
    for sp in sps:
        for di in sorted(sp.glob("*.dist-info")):
            stem = di.name[: -len(".dist-info")]
            name, _, version = stem.rpartition("-")
            meta_version, meta_name = version, name
            meta = di / "METADATA"
            if meta.is_file():
                for line in meta.read_text(errors="replace").splitlines():
                    if line.startswith("Version:"):
                        meta_version = line.split(":", 1)[1].strip()
                    elif line.startswith("Name:"):
                        meta_name = line.split(":", 1)[1].strip()
                    elif not line.strip():
                        break  # end of the header block
            editable, origin = False, ""
            du = di / "direct_url.json"
            if du.is_file():
                try:
                    d = json.loads(du.read_text())
                    editable = bool((d.get("dir_info") or {}).get("editable"))
                    origin = str(d.get("url") or "").removeprefix("file://")
                except (json.JSONDecodeError, OSError):
                    pass
            live = (source_version(Path(origin)) if editable and origin else None) or meta_version
            yield meta_name, {"version": live, "metadata_version": meta_version, "editable": editable,
                              "origin": origin, "site_packages": str(sp)}


def _read_dist_info(python_path: Path, dist_name: str) -> Optional[Dict[str, Any]]:
    """What `python_path` would import for `dist_name`: version + editable origin.

    Reads `<env>/lib/python3.*/site-packages/<name>-<ver>.dist-info` (METADATA
    `Version:`, `direct_url.json` `dir_info.editable` + `url`) — the facts
    `pip show` reports, without running the env. Delegates to `_iter_dist_infos`
    so the whole-env sweep (`read_cjm_set`) and the single-dist read agree."""
    want = _norm(dist_name)
    for name, truth in _iter_dist_infos(python_path):
        if _norm(name) == want:
            return truth
    return None


def read_cjm_set(python_path: Path) -> Dict[str, Dict[str, Any]]:  # dist name -> {version, editable, origin, site_packages}
    """The WHOLE cjm-* set the interpreter at `python_path` would import — the
    substrate, primitives, interface libs, adapter libs, the capability and any
    cjm-* utils — keyed by dist name, sorted. Finding 74ae00f5: the host lib is
    imported by every worker env yet named by no manifest, so env truth has to
    read the env, not the manifest's one capability (DEC f282571c)."""
    out: Dict[str, Dict[str, Any]] = {}
    for name, truth in _iter_dist_infos(python_path):
        if _norm(name).startswith("cjm_"):
            out[name] = truth
    return dict(sorted(out.items()))


def derive_mode(package_source: str) -> str:  # "dev" | "distribution"
    """Mode from a package spec (DEC f282571c): a filesystem path or an `-e `
    spec is dev (the code lives in a checkout); a PyPI spec or a git URL is
    distribution (the code is a release). An empty source — a manifest that
    predates package_source — reads as dev, which keeps the old verdict
    (NON-EDITABLE = refresh) for it."""
    src = package_source.strip()
    if not src or src.startswith("-e "):
        return "dev"
    if src.startswith("git+") or "://" in src:
        return "distribution"
    if src.startswith((".", "~", "/")) or "/" in src or "\\" in src:
        return "dev"
    return "distribution"


def env_mode(install: Dict[str, Any]) -> str:  # "dev" | "distribution"
    """The env's mode: the manifest's recorded `install.mode` when it is one of
    the two values, else derived from `install.package_source`."""
    recorded = str(install.get("mode") or "").strip()
    if recorded in ("dev", "distribution"):
        return recorded
    return derive_mode(str(install.get("package_source") or ""))


def host_substrate() -> Dict[str, Any]:  # {version, editable, origin, site_packages} of the substrate THIS interpreter runs
    """The host side of the launch-time check (DEC f282571c): the substrate this
    interpreter imports, read from its own dist-info exactly like a worker
    env's; falls back to the package's __version__ when no dist-info is found
    (a source-tree run)."""
    truth = _read_dist_info(Path(sys.executable), "cjm-substrate")
    if truth is not None:
        return truth
    from cjm_substrate import __version__
    return {"version": __version__, "editable": False, "origin": "", "site_packages": ""}


def checkouts_root() -> Optional[Path]:  # the parent of the host substrate's editable origin, else None
    """Dev mode's checkouts root: the parent of the host substrate's own editable
    origin — the one directory-shape fact READ from the install, never
    hardcoded (ruling aa00d43c (4)). None when the host runs a release; the
    dev verdict then treats every non-editable dist as a release."""
    host = host_substrate()
    if host.get("editable") and host.get("origin"):
        return Path(host["origin"]).resolve().parent
    return None


def _load_capability_manifest(path: Path) -> Optional[Dict[str, Any]]:
    """The parsed CAPABILITY manifest at `path`, or None for an adapter manifest
    (`unit: adapter` — a registration unit with no env of its own) or an
    unreadable file."""
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("unit") == "adapter":
        return None
    return data


def _verdict(row: Dict[str, Any], py: Path, cap: str, host: Dict[str, Any]) -> tuple:  # (status line, green?)
    """The MODE-AWARE status for one env row (DEC f282571c). dev: the capability
    editable from package_source AND every cjm-* dist that HAS a checkout under
    the checkouts root editable (a snapshot beside its checkout is the
    0.0.50-under-0.0.69 hazard; a release with no checkout is just a release).
    distribution: every cjm-* dist a release AND installed == the manifest's
    recorded pin. Both modes compare the env's substrate with the host's — that
    gap is what the launch-time check refuses."""
    mode, cjm_set, src = row["mode"], row["cjm_set"], row["package_source"].rstrip("/")
    cap_truth = _read_dist_info(py, cap)
    if cap_truth is None:
        return f"`{cap}` NOT INSTALLED in this env", False
    notes: List[str] = []
    ok = True
    sub = next((t for n, t in cjm_set.items() if _norm(n) == "cjm_substrate"), None)
    if mode == "distribution":
        editable = [n for n, t in cjm_set.items() if t["editable"]]
        if editable:
            notes.append("EDITABLE in a distribution env: " + ", ".join(editable)
                         + " — `cjm-ctl refresh --mode distribution` reinstalls from PyPI")
            ok = False
        resolved = row["resolved"]
        if not resolved:
            notes.append("no pins recorded — run `cjm-ctl refresh`")
            ok = False
        else:
            drift = [f"{n} {t['version']} (pin {resolved[n]})" for n, t in cjm_set.items()
                     if n in resolved and resolved[n] != t["version"]]
            unpinned = [n for n in cjm_set if n not in resolved]
            if drift:
                notes.append("installed != pin: " + ", ".join(drift) + " — `cjm-ctl refresh`")
                ok = False
            if unpinned:
                notes.append("unpinned: " + ", ".join(unpinned) + " — `cjm-ctl refresh` records them")
                ok = False
        if ok:
            notes.append(f"distribution: every cjm-* dist matches its pin ({len(cjm_set)} dists)")
    else:
        if not cap_truth["editable"]:
            notes.append(f"NON-EDITABLE {cap_truth['version']} — refresh with: {py} -m pip install -e "
                         f"{src or '<checkout>'} --no-deps (or `cjm-ctl refresh --only {cap}`)")
            ok = False
        else:
            same = bool(cap_truth["origin"] and src
                        and Path(cap_truth["origin"]).resolve() == Path(src).resolve())
            if same:
                notes.append("editable from package_source — edits are live on next process")
            else:
                notes.append(f"EDITABLE from elsewhere: {cap_truth['origin']}")
                ok = False
        root = checkouts_root()
        snapshots, releases = [], []
        for n, t in cjm_set.items():
            if t["editable"]:
                continue
            has_checkout = root is not None and (root / n / "pyproject.toml").is_file()
            (snapshots if has_checkout else releases).append(f"{n}={t['version']}")
        if snapshots:
            notes.append(f"{len(snapshots)} cjm-* dist(s) NON-EDITABLE with a checkout: " + " ".join(snapshots)
                         + f" — `cjm-ctl refresh --only {cap}`")
            ok = False
        if releases:
            notes.append("release(s) without a checkout: " + " ".join(releases))
        if (sub is not None and sub["editable"] and host.get("editable") and host.get("origin")
                and sub["origin"] and Path(sub["origin"]).resolve() != Path(host["origin"]).resolve()):
            notes.append(f"substrate editable from elsewhere: {sub['origin']} (host: {host['origin']})")
            ok = False
    if sub is not None and str(sub["version"]) != str(host["version"]):
        notes.append(f"substrate {sub['version']} vs host {host['version']} — the launch check will REFUSE")
        ok = False
    return "; ".join(notes), ok


def envs_for(
    lib: Optional[str],             # The lib to look for (dist name) — None = every worker env, whole cjm-* set
    workspaces_root: Path,          # Directory whose children are scanned for <ws>/.cjm/manifests/
    mode: Optional[str] = None,     # Force "dev" | "distribution" for every env; None = each manifest's own mode
) -> List[Dict[str, Any]]:  # One row per (workspace, capability manifest) that names or holds the lib
    """Every env the manifests say serves `lib` — or whose interpreter would
    import it (finding 74ae00f5: the substrate is in every worker env yet named
    by no manifest) — with what that env actually holds and a MODE-AWARE
    verdict (DEC f282571c): dev = editable from the checkout, distribution =
    installed == the manifest's recorded pins."""
    host = host_substrate()
    rows: List[Dict[str, Any]] = []
    for mdir in sorted(workspaces_root.glob("*/.cjm/manifests")):
        ws = mdir.parent.parent
        for mf in sorted(mdir.glob("*.json")):
            manifest = _load_capability_manifest(mf)
            if manifest is None:
                continue
            install = manifest.get("install") or {}
            code = manifest.get("code") or {}
            cap = str(code.get("name") or mf.stem)
            pp = str(install.get("python_path") or "")
            py = Path(pp) if pp else None
            present = py is not None and py.exists()
            cjm_set = read_cjm_set(py) if present else {}
            if lib is not None:
                named = _norm(mf.stem) == _norm(lib) or _norm(cap) == _norm(lib)
                held = any(_norm(n) == _norm(lib) for n in cjm_set)
                if not (named or held):
                    continue
            row: Dict[str, Any] = {
                "workspace": ws.name, "manifest": str(mf), "capability": cap,
                "python_path": pp, "conda_env": str(install.get("conda_env") or ""),
                "package_source": str(install.get("package_source") or ""),
                "manifest_version": str(code.get("version") or ""),
                "mode": mode or env_mode(install),
                "resolved": dict(install.get("resolved") or {}),
                "cjm_set": cjm_set, "host_substrate": host["version"],
            }
            if not pp:
                row["status"], row["ok"] = "NO python_path in manifest", False
            elif not present:
                row["status"], row["ok"] = "python_path MISSING on disk", False
            else:
                truth = _read_dist_info(py, lib if lib is not None else cap)
                if truth is not None:
                    row.update(truth)
                row["status"], row["ok"] = _verdict(row, py, cap, host)
            rows.append(row)
    return rows


def render_rows(lib: Optional[str], rows: List[Dict[str, Any]]) -> str:
    """The human sweep report + the post-edit checklist verdict line."""
    if not rows:
        if lib:
            return (f"no workspace manifest names `{lib}` and no worker env holds it under this "
                    "root — wrong --root, or the lib is in no worker env")
        return "no capability manifests under this root — wrong --root?"
    head = f"env truth for `{lib}`" if lib else "env truth — every worker env's cjm-* set (* = editable)"
    lines = [f"{head} — {len(rows)} manifest(s) · host cjm-substrate {rows[0]['host_substrate']}:"]
    needs = 0
    for r in rows:
        ok = bool(r.get("ok"))
        if not ok:
            needs += 1
        ver = r.get("version") or r.get("manifest_version") or "?"
        lines.append(f"  {'✓' if ok else '✗'} [{r['workspace']}] {r['conda_env'] or r['python_path'] or '-'}"
                     f" · {r['capability']} · {r['mode']} · installed {ver} · {r['status']}")
        if lib is None and r.get("cjm_set"):
            lines.append("      " + " ".join(f"{n}={t['version']}{'*' if t['editable'] else ''}"
                                             for n, t in r["cjm_set"].items()))
    lines.append("post-edit checklist: " +
                 ("every env is green for its mode — nothing to do."
                  if needs == 0 else
                  f"{needs} env(s) flagged — dev envs will NOT see your edits, distribution envs drift "
                  "from their pins; run `cjm-ctl refresh`, then relaunch running workers (fresh process)."))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Env truth sweep — every worker env vs what its interpreter would import "
                    "(the manifest = the only authority; mode-aware per DEC f282571c)")
    ap.add_argument("lib", nargs="?", default=None,
                    help="Dist name, e.g. cjm-capability-graph-sqlite or cjm-substrate "
                         "(omit = every env's whole cjm-* set)")
    ap.add_argument("--root", type=Path, default=None,
                    help="Workspaces root to scan (default: parent of the enclosing "
                         "workspace, else the cwd's parent)")
    ap.add_argument("--mode", choices=("dev", "distribution"), default=None,
                    help="Judge every env in this mode (default: each manifest's recorded / derived mode)")
    ap.add_argument("--json", action="store_true", help="Machine rows instead of the report")
    ap.add_argument("--strict", action="store_true", help="Exit 2 when any env is flagged (a gate)")
    args = ap.parse_args(argv)
    root = args.root
    if root is None:
        here = Path.cwd()
        ws = next((p for p in [here, *here.parents] if (p / "cjm.yaml").is_file()), None)
        root = (ws.parent if ws is not None else here.parent)
    rows = envs_for(args.lib, root, mode=args.mode)
    if args.json:
        print(json.dumps({"lib": args.lib, "root": str(root), "host_substrate": host_substrate(),
                          "rows": rows}, indent=2))
    else:
        print(f"root: {root}")
        print(render_rows(args.lib, rows))
    if not rows:
        return 1
    return 2 if args.strict and any(not r.get("ok") for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
