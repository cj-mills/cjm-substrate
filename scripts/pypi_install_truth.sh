#!/bin/bash
# Window-close ritual rung 3: the fresh-venv INSTALL-TRUTH gate — pip install the leaf apps
# + the four first-publishes from PyPI ONLY into a throwaway venv, then a neutral-cwd import
# sweep. Catches version-equal content drift and undeclared deps that rungs 1-2 cannot.
set -u
V=/tmp/claude-1000/-mnt-SN850X-8TB-EXT4-Projects-GitHub-cj-mills-cjm-substrate/75535c01-aefb-456a-a900-a5cdd492d9ab/scratchpad/venv-truth
rm -rf "$V"; python3 -m venv "$V" >/dev/null || { echo "venv FAILED"; exit 1; }
PIP="$V/bin/pip"; PY="$V/bin/python"
$PIP install -q --upgrade pip >/dev/null 2>&1
PKGS="cjm-transcription-qt==0.0.8 cjm-transcript-decomp-qt==0.0.8 cjm-transcript-correction-qt==0.0.20 cjm-workflow-hub-qt==0.0.4 cjm-context-graph-projection==0.0.76 cjm-capability-pysbd==0.0.1 cjm-capability-monitor-nvidia==0.0.25 cjm-sentence-segmentation-adapter-interface==0.0.1 cjm-speaker-diarization-adapter-interface==0.0.1"
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
