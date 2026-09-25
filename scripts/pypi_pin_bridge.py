"""Pin-bridge matrix over the 21 unpublished repos: every cjm-* floor each pins, the
local version of that dep, its PyPI latest, and whether the floor is LIVE on PyPI.
Derives the publish ORDER (a repo publishes only after every floor it pins is live)."""

import json
import re
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path("/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills")
UNPUB = """cjm-capability-demucs cjm-capability-ffmpeg cjm-capability-monitor-nvidia cjm-capability-pysbd
cjm-capability-voxtral-hf cjm-context-graph-layer cjm-context-graph-projection cjm-dev-graph-schema
cjm-python-decompose-core cjm-sentence-segmentation-adapter-interface cjm-speaker-diarization-adapter-interface
cjm-substrate cjm-substrate-qt-kit cjm-transcript-correction-core cjm-transcript-correction-qt
cjm-transcript-decomp-core cjm-transcript-decomp-qt cjm-transcript-graph-schema cjm-transcription-core
cjm-transcription-qt cjm-workflow-hub-qt""".split()


def vkey(v): return [int(x) if x.isdigit() else x for x in v.split(".")]


def local_version(repo):
    d = tomllib.loads((repo / "pyproject.toml").read_text())
    v = d["project"].get("version")
    if v is None:
        for init in repo.glob("*/__init__.py"):
            m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init.read_text())
            if m: return m.group(1)
    return v


_cache = {}


def pypi_releases(name):
    if name in _cache: return _cache[name]
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=15) as r:
            rel = sorted(json.load(r)["releases"], key=vkey)
    except urllib.error.HTTPError:
        rel = []
    _cache[name] = rel
    return rel


rows = []
for name in UNPUB:
    repo = ROOT / name
    d = tomllib.loads((repo / "pyproject.toml").read_text())
    deps = d["project"].get("dependencies", [])
    for spec in deps:
        m = re.match(r"\s*(cjm-[A-Za-z0-9_.-]+)\s*(\[.*?\])?\s*(>=|==|~=)?\s*([0-9][0-9.]*)?", spec)
        if not m: continue
        dep, _, op, floor = m.groups()
        rel = pypi_releases(dep)
        latest = rel[-1] if rel else "404"
        live = (floor is None) or (floor in rel)
        dep_repo = ROOT / dep
        lv = local_version(dep_repo) if (dep_repo / "pyproject.toml").is_file() else "-"
        rows.append((name, dep, f"{op or ''}{floor or ''}", lv, latest, "live" if live else "UNMET"))

print(f"{'repo':44} {'dep':44} {'pin':10} {'local':8} {'pypi':8} state")
for r in rows:
    if r[5] == "UNMET":
        print(f"{r[0]:44} {r[1]:44} {r[2]:10} {r[3]:8} {r[4]:8} {r[5]}")
print()
unmet = {}
for r in rows:
    if r[5] == "UNMET": unmet.setdefault(r[0], set()).add(r[1])
print("Repos with NO unmet floor (publish first):")
for n in UNPUB:
    if n not in unmet: print("  ", n)
print("Repos with unmet floors -> waits on:")
for n in UNPUB:
    if n in unmet: print("  ", n, "->", ", ".join(sorted(unmet[n])))
