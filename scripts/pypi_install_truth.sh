#!/bin/bash
# Window-close ritual rung 4: the fresh-venv INSTALL-TRUTH gate — pip install the leaf apps
# + the window's first publishes and bumped workers from PyPI ONLY into a throwaway venv, then a neutral-cwd import
# sweep. Catches version-equal content drift and undeclared deps that rungs 1-2 cannot.
set -u
# The venv lives under $1 (a scratch dir) or a fresh temp dir; the set comes from the pin
# bridge (the leaf apps + this window's first publishes + its bumped workers), never retyped.
V="${1:-$(mktemp -d)}/venv-truth"
HOSTPY=/home/innom-dt/miniforge3/envs/cjm-substrate/bin/python
PKGS=$($HOSTPY "$(dirname "$(readlink -f "$0")")/pypi_pin_bridge.py" --install-set | tr "\n" " ") || { echo "PIN BRIDGE FAILED"; exit 1; }
echo "install set: $PKGS"
rm -rf "$V"; python3 -m venv "$V" >/dev/null || { echo "venv FAILED"; exit 1; }
PIP="$V/bin/pip"; PY="$V/bin/python"
$PIP install -q --upgrade pip >/dev/null 2>&1
if $PIP install -q $PKGS >"$V.install.log" 2>&1; then echo "INSTALL OK"; else echo "INSTALL FAILED:"; grep -E "ERROR|No matching|Could not" "$V.install.log" | head -5; fi
cd /
echo "--- cjm-* resolved in the fresh venv:"
$PIP list --format json | $PY -c "import json,sys; print(' '.join(f\"{p['name']}={p['version']}\" for p in sorted(json.load(sys.stdin), key=lambda p:p['name']) if p['name'].startswith('cjm-')))"
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
echo "INSTALL-TRUTH-DONE"
