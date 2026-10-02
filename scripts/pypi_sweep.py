"""Local-vs-PyPI version sweep over every cjm-* repo with a pyproject — the release-rung
sweep of the PyPI window-close ritual (publish-cascade lock 3c87a5e9, rung 1).

Reads each repo's version (static pyproject `version`, else `__version__`) and asks the
PyPI JSON API whether that exact release exists: `ok`, `BUMP-UNPUBLISHED` (an existing
package with a newer local version — rides the uncapped lane) or `FIRST-PUBLISH` (404 —
counts against the ~4/week new-project cap). Repos ARCHIVED on GitHub are skipped by the
archival disposition (their local deltas are deliberate non-publishes). Blind spots the
ritual's later rungs cover: version-equal CONTENT drift and undeclared dependencies.

The sweep is the window's ONE inventory: the pin bridge, the build rung and the
install-truth rung all derive their repo lists from `sweep()` (session 2026-10-02_11-09-11 —
the lists used to be retyped per window).

    python scripts/pypi_sweep.py           # unpublished rows only
    python scripts/pypi_sweep.py -v        # every repo
    python scripts/pypi_sweep.py --names   # the unpublished repo dirs, one per line (the build rung reads it)
"""

import json
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path("/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills")
OWNER = "cj-mills"
_releases: Dict[str, List[str]] = {}


def vkey(v: str) -> list:
    return [int(x) if x.isdigit() else x for x in v.split(".")]


def archived_repos() -> set:
    """Names of the owner's GitHub repos that are archived (one gh call; empty if gh is absent)."""
    try:
        out = subprocess.run(["gh", "repo", "list", OWNER, "--limit", "300", "--json", "name,isArchived"],
                             capture_output=True, text=True, check=True).stdout
        return {r["name"] for r in json.loads(out) if r.get("isArchived")}
    except (OSError, subprocess.CalledProcessError, ValueError) as e:
        print(f"(gh unavailable — archived repos not excluded: {e})", file=sys.stderr)
        return set()


def local_version(repo: Path) -> Optional[str]:
    """A repo's version: the static pyproject `version`, else the package's `__version__`."""
    proj = tomllib.loads((repo / "pyproject.toml").read_text()).get("project", {})
    version = proj.get("version")
    if version is None:
        for init in repo.glob("*/__init__.py"):
            m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init.read_text())
            if m:
                return m.group(1)
    return version


def pypi_releases(name: str) -> Optional[List[str]]:
    """The distribution's releases on PyPI, sorted (None = 404, never published). Cached."""
    if name not in _releases:
        try:
            with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=15) as r:
                _releases[name] = sorted(json.load(r)["releases"], key=vkey)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            _releases[name] = None
    return _releases[name]


def sweep() -> List[dict]:
    """One row per non-archived cjm-* repo with a pyproject: repo, name, version, latest, state."""
    archived = archived_repos()
    rows = []
    for repo in sorted(ROOT.glob("cjm-*")):
        pp = repo / "pyproject.toml"
        if not pp.is_file() or repo.name in archived:
            continue
        name = tomllib.loads(pp.read_text()).get("project", {}).get("name")
        if not name:
            continue
        version = local_version(repo)
        rel = pypi_releases(name)
        state = ("FIRST-PUBLISH" if rel is None else "ok" if version in rel else "BUMP-UNPUBLISHED")
        rows.append({"repo": repo.name, "name": name, "version": version,
                     "latest": "404" if rel is None else (rel[-1] if rel else "-"), "state": state})
    sweep.archived = len(archived)
    return rows


def main(argv: List[str]) -> int:
    rows = sweep()
    if "--names" in argv:
        print("\n".join(r["repo"] for r in rows if r["state"] != "ok"))
        return 0
    print(f"{'repo':40} {'local':9} {'pypi':9} state")
    for r in rows:
        if r["state"] != "ok" or "-v" in argv:
            print(f"{r['repo']:40} {str(r['version']):9} {str(r['latest']):9} {r['state']}")
    print(f"\n{len(rows)} repos · {sum(1 for r in rows if r['state'] != 'ok')} unpublished"
          f" · {sum(1 for r in rows if r['state'] == 'FIRST-PUBLISH')} first-publish (weekly cap ~4)"
          f" · {sweep.archived} archived repo(s) skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
