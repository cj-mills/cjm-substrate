"""The worker-env REFRESH verb (DEC f282571c, work item dbbaa8b4): every worker
env upgradable from the published closure — on this machine and on another.

`cjm-ctl refresh` walks a workspace's CAPABILITY manifests (the manifest's
`install.python_path` is the only authority on which interpreter serves a
capability — the env-truth rule), decides each env's MODE, plans the pip
work, runs it, re-records the manifest (code section + the whole-env
`resolved` set + `mode` + `refreshed_at`) and names every RUNNING worker
process that still serves the old code. Two modes, one verb:

- dev: every cjm-* dist the env holds is editable-installed `--no-deps` from
  its checkout — the editable origin it already has, else `<root>/<dist>`
  (root defaults to the parent of the host substrate's own editable origin:
  nothing resolves from this machine's directory shape, ruling aa00d43c (4)).
- distribution: ONE `pip install --upgrade` of the substrate spec (default
  `cjm-substrate==<host version>` — the host pins the worker, the contract
  the launch-time check enforces) + the capabilities yaml entry's interface
  libs, adapter libs and package, then `--upgrade` of every other cjm-* dist
  the env holds (only-if-needed for their non-cjm deps); a path-valued yaml
  entry is REFUSED (pass the PUBLIC yaml).

Planning is pure (`plan_env`) and execution takes a runner, so the plan is
testable without pip. `derive_public_capabilities` is the yaml projection
that rides the one-channel ruling 8299fb9d: dev paths -> PyPI specs with
floors, env_file paths -> the checkout's raw GitHub URL; `--check-pypi`
reads each floor's per-release endpoint (the register's first-publish
staging gate as a verb).
"""

import json
import re
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from cjm_substrate.utils.envtruth import _norm, checkouts_root, derive_mode, source_version

Runner = Callable[[List[str], bool], Tuple[int, str]]  # (argv, capture) -> (returncode, stdout)


def run_argv(argv: List[str], capture: bool = False) -> Tuple[int, str]:
    """The default runner: stream when the operator should watch (pip installs),
    capture when the output is data (pip list / check, the import sweep)."""
    if capture:
        r = subprocess.run(argv, capture_output=True, text=True)
        return r.returncode, r.stdout + (("\n" + r.stderr) if r.stderr else "")
    r = subprocess.run(argv, text=True)
    return r.returncode, ""


@dataclass
class EnvPlan:
    """One env's refresh plan — every command the verb will run, in order, plus
    what it decided and why. `refusal` set = nothing runs for this env."""
    capability: str
    conda_env: str
    python_path: str
    mode: str
    commands: List[List[str]] = field(default_factory=list)  # argv lists, run in order
    notes: List[str] = field(default_factory=list)           # what each command is for
    skipped: List[str] = field(default_factory=list)         # dists left as installed (dev: no checkout)
    refusal: Optional[str] = None
    package_source: str = ""                                  # the spec _generate_manifest re-records

    def render(self) -> str:
        lines = [f"[{self.conda_env or self.python_path}] {self.capability} · {self.mode}"]
        if self.refusal:
            lines.append(f"   REFUSED: {self.refusal}")
            return "\n".join(lines)
        for note, cmd in zip(self.notes, self.commands):
            lines.append(f"   {note}: {' '.join(cmd)}")
        if self.skipped:
            lines.append(f"   left as installed (no checkout): {' '.join(self.skipped)}")
        return "\n".join(lines)


def default_checkouts_root() -> Optional[Path]:
    """Dev mode's checkouts root when none is given — env truth's
    `checkouts_root` (the parent of the host substrate's own editable origin:
    the one directory-shape fact READ from the install, never hardcoded)."""
    return checkouts_root()


def find_checkout(dist: str, truth: Dict[str, Any], root: Optional[Path]) -> Optional[Path]:
    """The checkout a dist refreshes from in dev mode: its editable origin when
    it has one (and the tree still exists), else `<root>/<dist>` when that
    holds a pyproject.toml, else None (left as installed)."""
    origin = str(truth.get("origin") or "")
    if truth.get("editable") and origin and (Path(origin) / "pyproject.toml").is_file():
        return Path(origin)
    if root is not None and (root / dist / "pyproject.toml").is_file():
        return root / dist
    return None


def yaml_entry_for(config: Optional[Dict[str, Any]], conda_env: str) -> Optional[Dict[str, Any]]:
    """The capabilities-yaml entry that built this env — matched on `env_name`
    == the manifest's `install.conda_env` (the yaml's `name` and the manifest
    stem differ: sys-mon-nvidia vs cjm-capability-monitor-nvidia)."""
    for cap in (config or {}).get("capabilities", []) or []:
        if isinstance(cap, dict) and str(cap.get("env_name") or "") == conda_env:
            return cap
    return None


def yaml_specs(entry: Dict[str, Any]) -> List[str]:
    """Every pip spec a yaml entry installs into its env: interface libs, adapter
    libs, then the package (install-all's order)."""
    specs: List[str] = list(entry.get("interface_libs") or [])
    specs += [a["lib"] for a in (entry.get("adapters") or []) if isinstance(a, dict) and a.get("lib")]
    if entry.get("package"):
        specs.append(str(entry["package"]))
    return specs


def plan_env(
    manifest: Dict[str, Any],                 # the parsed capability manifest (install + code sections)
    mode: str,                                # "dev" | "distribution"
    cjm_set: Dict[str, Dict[str, Any]],       # read_cjm_set(python_path) — what the env holds now
    *,
    yaml_entry: Optional[Dict[str, Any]] = None,   # the capabilities-yaml entry (distribution mode's specs)
    substrate_spec: Optional[str] = None,          # override for the substrate (dev: a checkout path; distribution: a pip spec)
    root: Optional[Path] = None,                   # dev: checkouts root for dists without an editable origin
    host_version: str = "",                        # distribution: the default substrate pin
) -> EnvPlan:
    """The pure planning step: which pip commands refresh this env, in order."""
    install = manifest.get("install") or {}
    code = manifest.get("code") or {}
    plan = EnvPlan(capability=str(code.get("name") or ""), conda_env=str(install.get("conda_env") or ""),
                   python_path=str(install.get("python_path") or ""), mode=mode,
                   package_source=str(install.get("package_source") or ""))
    py = plan.python_path
    pip = [py, "-m", "pip"]
    if mode == "dev":
        for dist, truth in cjm_set.items():
            checkout = (Path(substrate_spec) if substrate_spec and _norm(dist) == "cjm_substrate"
                        else find_checkout(dist, truth, root))
            if checkout is None:
                plan.skipped.append(dist)
                continue
            plan.commands.append(pip + ["install", "-e", str(checkout), "--no-deps", "-q"])
            plan.notes.append(f"editable {dist}")
        if not plan.commands and not plan.skipped:
            plan.refusal = "the env holds no cjm-* dist — not a worker env"
    elif mode == "distribution":
        if yaml_entry is None:
            plan.refusal = ("no capabilities-yaml entry with env_name == this manifest's conda_env — "
                            "pass --capabilities <the public yaml>")
            return plan
        specs = yaml_specs(yaml_entry)
        paths = [s for s in specs if derive_mode(s) == "dev"]
        if paths:
            plan.refusal = ("distribution mode needs PyPI specs but the yaml names checkouts: "
                            + ", ".join(paths) + " — derive the public yaml first "
                            "(`cjm-ctl derive-public-capabilities`)")
            return plan
        sub = substrate_spec or (f"cjm-substrate=={host_version}" if host_version else "cjm-substrate")
        plan.commands.append(pip + ["install", "--upgrade", sub, *specs])
        plan.notes.append("release closure")
        named = {_norm(sub.split("=")[0].split(">")[0].split("<")[0].split("[")[0])}
        named |= {_norm(re.split(r"[=<>!~\[; ]", s, 1)[0]) for s in specs}
        rest = [d for d in cjm_set if _norm(d) not in named]
        if rest:
            plan.commands.append(pip + ["install", "--upgrade", *rest])
            plan.notes.append("every other cjm-* dist the env holds")
        plan.package_source = str(yaml_entry.get("package") or plan.package_source)
    else:
        plan.refusal = f"unknown mode {mode!r}"
        return plan
    if plan.commands:
        plan.commands.append(pip + ["check"])
        plan.notes.append("dependency check")
    return plan


def execute_plan(plan: EnvPlan, runner: Runner = run_argv) -> List[Tuple[str, int, str]]:
    """Run the plan's commands in order; a failed install stops the env (a
    half-refreshed env is reported, not hidden). Returns (note, rc, output)."""
    results: List[Tuple[str, int, str]] = []
    for note, cmd in zip(plan.notes, plan.commands):
        capture = note == "dependency check"
        rc, out = runner(cmd, capture)
        results.append((note, rc, out.strip()))
        if rc != 0 and not capture:
            break
    return results


def running_workers(python_path: str) -> List[Dict[str, Any]]:
    """Every live `cjm_substrate.core.worker` process whose interpreter lives in
    this env — they keep serving the OLD code until their host relaunches them
    (the launcher item 5ee9cbb0 shows the banner; refresh only names them)."""
    import psutil
    env_root = str(Path(python_path).resolve().parent.parent)
    found: List[Dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "cmdline", "exe"]):
        try:
            cmd = list(proc.info.get("cmdline") or [])
            exe = str(proc.info.get("exe") or "")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if "cjm_substrate.core.worker" not in cmd:
            continue
        argv0 = cmd[0] if cmd else ""
        if Path(argv0).exists():
            argv0 = str(Path(argv0).resolve())
        if exe.startswith(env_root) or argv0.startswith(env_root):
            found.append({"pid": proc.info["pid"], "cmdline": " ".join(cmd)})
    return found


_IMPORT_SWEEP = r'''
import importlib, importlib.metadata as md, json
bad, n = [], 0
for d in md.distributions():
    name = d.metadata["Name"]
    if not name.startswith("cjm-"): continue
    tops = (d.read_text("top_level.txt") or "").split()
    if not tops:
        tops = sorted({f.parts[0] for f in (d.files or []) if f.suffix == ".py" and len(f.parts) > 1 and not f.parts[0].endswith(".dist-info")})
    for t in tops:
        if t in ("tests", "tests_manual", "scripts"): continue
        n += 1
        try:
            importlib.import_module(t)
        except Exception as e:
            bad.append(f"{name} :: import {t} -> {type(e).__name__}: {e}")
print("IMPORT-SWEEP " + json.dumps({"total": n, "failures": bad}))
'''


def import_sweep(python_path: str, runner: Runner = run_argv) -> Dict[str, Any]:
    """Rung 3 of the window-close ritual as a verb: from a NEUTRAL cwd (a temp
    dir — a repo cwd shadows site-packages), import every top-level module of
    every cjm-* dist the env holds. Returns {total, failures}."""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "sweep.py"
        script.write_text(_IMPORT_SWEEP)
        rc, out = runner([python_path, str(script)], True)
    for line in out.splitlines():
        if line.startswith("IMPORT-SWEEP "):
            return json.loads(line[len("IMPORT-SWEEP "):])
    return {"total": 0, "failures": [f"sweep did not report (rc {rc}): {out[-400:]}"]}


# --- the public capabilities yaml (ruling 8299fb9d) -------------------------

def checkout_root(path: Path) -> Optional[Path]:
    """The checkout that owns `path`: the nearest ancestor (or `path` itself)
    holding a pyproject.toml."""
    start = path if path.is_dir() else path.parent
    for p in [start, *start.parents]:
        if (p / "pyproject.toml").is_file():
            return p
    return None


def checkout_dist_version(checkout: Path) -> Tuple[str, str]:
    """(dist name, version) from a checkout's pyproject.toml — the name from
    `project.name`, the version through env truth's `source_version` (static
    `project.version`, else the setuptools dynamic `attr`)."""
    data = tomllib.loads((checkout / "pyproject.toml").read_text())
    name = str((data.get("project") or {}).get("name") or checkout.name)
    return name, source_version(checkout) or ""


def checkout_remote(checkout: Path) -> Tuple[str, str]:
    """(origin url, default branch) of a checkout — `git config` for the url,
    the remote HEAD for the branch (falls back to "main")."""
    def git(*args: str) -> str:
        r = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    url = git("config", "--get", "remote.origin.url")
    head = git("symbolic-ref", "--short", "refs/remotes/origin/HEAD")  # "origin/main"
    branch = head.split("/", 1)[1] if "/" in head else "main"
    return url, branch


def raw_github_url(remote_url: str, branch: str, relpath: str) -> str:
    """A GitHub origin (ssh or https, with or without .git) -> the raw file URL
    a fresh machine downloads the environment.yml from."""
    m = re.match(r"^(?:git@github\.com:|https?://github\.com/)([^/]+)/([^/]+?)(?:\.git)?/?$", remote_url.strip())
    if not m:
        raise ValueError(f"not a GitHub origin: {remote_url!r}")
    owner, repo = m.group(1), m.group(2)
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{relpath}"


def public_spec(path_spec: str, dist_of: Callable[[Path], Tuple[str, str]], floor: bool) -> str:
    """A dev path spec -> `<dist>>=<version>` (the version the checkout
    carries = the floor a consumer needs), or the bare name without a floor."""
    checkout = checkout_root(Path(path_spec.removeprefix("-e ").strip()))
    if checkout is None:
        raise ValueError(f"no pyproject.toml under {path_spec!r}")
    name, version = dist_of(checkout)
    return f"{name}>={version}" if floor and version else name


def derive_public_capabilities(
    config: Dict[str, Any],                                           # the dev yaml, parsed
    *,
    dist_of: Callable[[Path], Tuple[str, str]] = checkout_dist_version,
    remote_of: Callable[[Path], Tuple[str, str]] = checkout_remote,
    floor: bool = True,
) -> Dict[str, Any]:
    """The public yaml: every path-valued `package` / `interface_libs[]` /
    `adapters[].lib` becomes a PyPI spec with a floor, every path-valued
    `env_file` the checkout's raw GitHub URL; specs that are already public
    pass through; `name`, `env_name`, `impl` and `python_version` are kept."""
    out: List[Dict[str, Any]] = []
    for cap in config.get("capabilities", []) or []:
        entry: Dict[str, Any] = {}
        for key, val in cap.items():
            if key == "env_file" and isinstance(val, str) and derive_mode(val) == "dev":
                p = Path(val)
                root = checkout_root(p)
                if root is None:
                    raise ValueError(f"env_file {val!r} is not inside a checkout")
                url, branch = remote_of(root)
                entry[key] = raw_github_url(url, branch, str(p.resolve().relative_to(root.resolve())))
            elif key == "package" and isinstance(val, str) and derive_mode(val) == "dev":
                entry[key] = public_spec(val, dist_of, floor)
            elif key == "interface_libs" and isinstance(val, list):
                entry[key] = [public_spec(v, dist_of, floor) if isinstance(v, str) and derive_mode(v) == "dev" else v
                              for v in val]
            elif key == "adapters" and isinstance(val, list):
                adapters = []
                for a in val:
                    a2 = dict(a) if isinstance(a, dict) else a
                    if isinstance(a2, dict) and isinstance(a2.get("lib"), str) and derive_mode(a2["lib"]) == "dev":
                        a2["lib"] = public_spec(a2["lib"], dist_of, floor)
                    adapters.append(a2)
                entry[key] = adapters
            else:
                entry[key] = val
        out.append(entry)
    return {"capabilities": out}


def spec_floors(config: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Every (dist, floor version) a public yaml pins with `>=`/`==` — the set
    --check-pypi reads against the per-release endpoints."""
    floors: List[Tuple[str, str]] = []
    for cap in config.get("capabilities", []) or []:
        for spec in yaml_specs(cap):
            m = re.match(r"^\s*([A-Za-z0-9_.\-]+)\s*(?:>=|==)\s*([^\s,;]+)", spec)
            if m:
                floors.append((m.group(1), m.group(2)))
    return sorted(set(floors))


def pypi_release_exists(name: str, version: str, timeout: float = 10.0) -> bool:
    """The register's first-publish gate: read the PER-RELEASE endpoint (the
    'latest' JSON lags the CDN) — True when that exact release is live."""
    try:
        with urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=timeout) as r:
            return r.status == 200
    except HTTPError as e:
        if e.code == 404:
            return False
        raise
    except URLError:
        raise


def render_public_yaml(public: Dict[str, Any], source_name: str) -> str:
    """The yaml text with its provenance header — a projection (regenerate it
    after every publish window), never hand-edited."""
    import yaml
    header = (f"# PUBLIC capabilities yaml — PROJECTED from {source_name} by "
              f"`cjm-ctl derive-public-capabilities` (ruling 8299fb9d: one channel, PyPI specs with\n"
              f"# floors, env_file = the capability repo's raw environment.yml). Regenerate after every\n"
              f"# publish window; the dev yaml with checkout paths stays the --capabilities override.\n")
    return header + yaml.safe_dump(public, sort_keys=False, allow_unicode=True, width=100)
