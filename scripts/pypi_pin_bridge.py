"""Pin-bridge matrix over the window's unpublished repos (the sweep's inventory): every
cjm-* floor each pins, the local version of that dep, its PyPI latest, and whether the
floor is LIVE on PyPI. Derives the publish WAVES (a repo uploads only after every floor it
pins is live — so a wave holds the repos whose unmet floors all sit in earlier waves) and
the install-truth set (rung 4: every app or CLI repo no other cjm-* repo pins — the leaves
whose install pulls the rest from PyPI — plus every first publish and every capability
worker or adapter interface this window publishes, at their local versions).

    python scripts/pypi_pin_bridge.py                 # unmet floors + the waves
    python scripts/pypi_pin_bridge.py --install-set   # name==version per line (install-truth reads it)
"""

import re
import sys
import tomllib
from pathlib import Path
from typing import Dict, List, Set

from pypi_sweep import local_version, pypi_releases, ROOT, sweep, vkey

# run as a script, Python puts this directory first on sys.path, so pypi_sweep imports as a sibling

# a pin may spell the distribution with underscores (cjm_context_graph_primitives); PyPI
# normalizes it, so the bridge does too
_PIN = re.compile(r"\s*(cjm[-_][A-Za-z0-9_.-]+)\s*(\[.*?\])?\s*(>=|==|~=)?\s*([0-9][0-9.]*)?")


def cjm_pins(repo: Path) -> List[tuple]:
    """(dep, op, floor) for every cjm-* dependency the repo's pyproject declares."""
    deps = tomllib.loads((repo / "pyproject.toml").read_text())["project"].get("dependencies", [])
    out = []
    for spec in deps:
        m = _PIN.match(spec)
        if m:
            dep, _, op, floor = m.groups()
            out.append((dep.replace("_", "-").lower(), op or "", floor))
    return out


def main(argv: List[str]) -> int:
    rows = sweep()
    unpub = [r for r in rows if r["state"] != "ok"]
    names = {r["repo"] for r in unpub}
    matrix = []
    unmet: Dict[str, Set[str]] = {}
    for r in unpub:
        for dep, op, floor in cjm_pins(ROOT / r["repo"]):
            rel = pypi_releases(dep) or []
            # a floor is live when PyPI carries a release that satisfies the pin
            live = (floor is None or floor in rel
                    or (op in (">=", "~=") and any(vkey(v) >= vkey(floor) for v in rel)))
            dep_repo = ROOT / dep
            lv = local_version(dep_repo) if (dep_repo / "pyproject.toml").is_file() else "-"
            matrix.append((r["repo"], dep, f"{op}{floor or ''}", lv, rel[-1] if rel else "404", live))
            if not live:
                unmet.setdefault(r["repo"], set()).add(dep)
    if "--install-set" in argv:
        pinned = {dep for r in rows for dep, _, _ in cjm_pins(ROOT / r["repo"])}
        for r in rows:
            # capability workers and adapter interfaces load through manifests and are never
            # pinned, so every one reads as a leaf -- but each runs in its own worker env and
            # co-installing them all would pull several model stacks into one venv: they join
            # the set only when this window publishes them
            worker = r["repo"].startswith("cjm-capability-") or r["repo"].endswith("-adapter-interface")
            leaf = r["repo"] not in pinned and not worker
            if leaf or r["state"] == "FIRST-PUBLISH" or (worker and r["state"] != "ok"):
                print(f"{r['name']}=={r['version']}")
        return 0
    print(f"{'repo':44} {'dep':44} {'pin':10} {'local':8} {'pypi':8} state")
    for m in matrix:
        if not m[5]:
            print(f"{m[0]:44} {m[1]:44} {m[2]:10} {m[3]:8} {m[4]:8} UNMET")
    stray = sorted({d for deps in unmet.values() for d in deps} - names)
    if stray:
        print(f"\n⚠ unmet floors NO repo in this window publishes: {', '.join(stray)} "
              "(a floor too high, or a dep outside the window) — fix before uploading")
    print("\nPublish waves (upload a wave, verify its per-release endpoints, then the next):")
    placed: Set[str] = set()
    wave = 0
    while len(placed) < len(names):
        ready = sorted(n for n in names - placed if unmet.get(n, set()) & names <= placed)
        if not ready:
            print(f"  ⚠ a cycle among: {', '.join(sorted(names - placed))}")
            break
        wave += 1
        print(f"  wave {wave}: {' '.join(ready)}")
        placed |= set(ready)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
