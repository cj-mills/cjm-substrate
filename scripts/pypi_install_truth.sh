#!/bin/bash
# Window-close ritual rung 4: the fresh-venv INSTALL-TRUTH gate — pip install the leaf apps
# + the window's first publishes and bumped workers from PyPI ONLY into a throwaway venv, then a neutral-cwd import
# sweep. Catches version-equal content drift and undeclared deps that rungs 1-2 cannot.
set -u
# The venv lives under $1 (a scratch dir) or a fresh temp dir; the set comes from the pin
# bridge (the leaf apps + this window's first publishes + its bumped workers), never retyped.
V="${1:-$(mktemp -d)}/venv-truth"
HOSTPY=/home/innom-dt/miniforge3/envs/cjm-substrate/bin/python
# $2 = the window's first upload date (default today, UTC): its releases stay in the set once live
SINCE="${2:-$(date -u +%Y-%m-%d)}"
PKGS=$($HOSTPY "$(dirname "$(readlink -f "$0")")/pypi_pin_bridge.py" --install-set --since "$SINCE" | tr "\n" " ") || { echo "PIN BRIDGE FAILED"; exit 1; }
echo "install set: $PKGS"
rm -rf "$V"; python3 -m venv "$V" >/dev/null || { echo "venv FAILED"; exit 1; }
PIP="$V/bin/pip"; PY="$V/bin/python"
$PIP install -q --upgrade pip >/dev/null 2>&1
# --no-cache-dir: a cached index page hides a release that is already live, and the gate then
# tests the PREVIOUS versions and passes (window 2026-10-10 resolved projection 0.0.81 over a live 0.0.82)
if $PIP install -q --no-cache-dir $PKGS >"$V.install.log" 2>&1; then echo "INSTALL OK"; else echo "INSTALL FAILED:"; grep -E "ERROR|No matching|Could not" "$V.install.log" | head -5; fi
cd /
echo "--- cjm-* resolved in the fresh venv:"
$PIP list --format json | $PY -c "import json,sys; print(' '.join(f\"{p['name']}={p['version']}\" for p in sorted(json.load(sys.stdin), key=lambda p:p['name']) if p['name'].startswith('cjm-')))"
echo "--- each cjm-* dist against its local repo version (a resolve below local = a stale index or an unpublished bump):"
STALE=$($PIP list --format json | $HOSTPY -c "
import json, sys, subprocess
from pathlib import Path
sweep = Path('$(dirname "$(readlink -f "$0")")') / 'pypi_sweep.py'
local = {}
for line in subprocess.run([sys.executable, str(sweep), '-v'], capture_output=True, text=True).stdout.splitlines():
    f = line.split()
    if len(f) >= 3 and f[0].startswith('cjm-'):
        local[f[0]] = f[1]
key = lambda v: tuple(int(x) for x in v.split('.') if x.isdigit())
for p in json.load(sys.stdin):
    n, v = p['name'], p['version']
    if n in local and key(v) < key(local[n]):
        print(f'  STALE {n}: resolved {v}, local {local[n]}')
")
if [ -n "$STALE" ]; then echo "$STALE"; else echo "  every cjm-* dist resolved at its local version"; fi
echo "--- neutral-cwd import sweep (every top-level module of every cjm-* dist):"
$PY - <<'EOF'
import importlib, importlib.metadata as md, sys
bad = 0; n = 0
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
            bad += 1; print(f"  ✗ {name} :: import {t} -> {type(e).__name__}: {e}")
print(f"  {n - bad}/{n} top-level modules import clean")
EOF
$PIP check 2>&1 | head -5
[ -n "$STALE" ] && { echo "INSTALL-TRUTH-STALE"; exit 1; }
echo "INSTALL-TRUTH-DONE"
