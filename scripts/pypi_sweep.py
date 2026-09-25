"""Local-vs-PyPI version sweep over every cjm-* repo with a pyproject — the release-rung
sweep of the PyPI window-close ritual (publish-cascade lock 3c87a5e9, rung 1).

Reads each repo's version (static pyproject `version`, else `__version__`) and asks the
PyPI JSON API whether that exact release exists: `ok`, `BUMP-UNPUBLISHED` (an existing
package with a newer local version — rides the uncapped lane) or `FIRST-PUBLISH` (404 —
counts against the ~4/week new-project cap). Repos ARCHIVED on GitHub are skipped by the
archival disposition (their local deltas are deliberate non-publishes). Blind spots the
ritual's later rungs cover: version-equal CONTENT drift and undeclared dependencies.

    python scripts/pypi_sweep.py        # unpublished rows only
    python scripts/pypi_sweep.py -v     # every repo
"""
import json, re, subprocess, sys, tomllib, urllib.request, urllib.error
from pathlib import Path

ROOT = Path("/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills")
OWNER = "cj-mills"


def archived_repos() -> set:
    """Names of the owner's GitHub repos that are archived (one gh call; empty if gh is absent)."""
    try:
        out = subprocess.run(["gh", "repo", "list", OWNER, "--limit", "300", "--json", "name,isArchived"],
                             capture_output=True, text=True, check=True).stdout
        return {r["name"] for r in json.loads(out) if r.get("isArchived")}
    except (OSError, subprocess.CalledProcessError, ValueError) as e:
        print(f"(gh unavailable — archived repos not excluded: {e})", file=sys.stderr)
        return set()


archived = archived_repos()
rows = []
for repo in sorted(ROOT.glob("cjm-*")):
    pp = repo / "pyproject.toml"
    if not pp.is_file():
        continue
    if repo.name in archived:
        continue
    data = tomllib.loads(pp.read_text())
    proj = data.get("project", {})
    name = proj.get("name")
    if not name:
        continue
    version = proj.get("version")
    if version is None:
        # dynamic version: read __version__ from the package
        for init in repo.glob("*/__init__.py"):
            m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init.read_text())
            if m:
                version = m.group(1)
                break
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=15) as r:
            info = json.load(r)
        releases = sorted(info["releases"], key=lambda v: [int(x) if x.isdigit() else x for x in v.split(".")])
        latest = releases[-1] if releases else None
        on_pypi = version in info["releases"]
    except urllib.error.HTTPError as e:
        latest, on_pypi = ("404" if e.code == 404 else f"http{e.code}"), False
    rows.append((repo.name, name, version, latest, on_pypi))

print(f"{'repo':40} {'local':9} {'pypi':9} state")
for repo, name, version, latest, on_pypi in rows:
    state = "ok" if on_pypi else ("FIRST-PUBLISH" if latest == "404" else "BUMP-UNPUBLISHED")
    if state != "ok" or "-v" in sys.argv:
        print(f"{repo:40} {str(version):9} {str(latest):9} {state}")
print(f"\n{len(rows)} repos · {sum(1 for r in rows if not r[4])} unpublished"
      f" · {sum(1 for r in rows if r[3] == '404')} first-publish (weekly cap ~4)"
      f" · {len(archived)} archived repo(s) skipped")
