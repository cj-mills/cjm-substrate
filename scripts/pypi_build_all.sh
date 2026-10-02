#!/bin/bash
# Build + twine-check every unpublished repo the sweep names (no upload). One line per repo:
#   repo  local-version  wheel-version  twine-verdict  wheel-file
PY=/home/innom-dt/miniforge3/envs/cjm-substrate/bin/python
ROOT=/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills
# The window's repos come from the sweep (its one inventory), never a retyped list
REPOS=$($PY "$(dirname "$(readlink -f "$0")")/pypi_sweep.py" --names) || { echo "SWEEP FAILED"; exit 1; }
for r in $REPOS; do
  cd "$ROOT/$r" || { echo "$r MISSING"; continue; }
  rm -rf dist
  if ! $PY -m build >/tmp/claude-1000/build_$r.log 2>&1; then echo "$r BUILD-FAILED (see /tmp/claude-1000/build_$r.log)"; continue; fi
  whl=$(ls dist/*.whl | head -1)
  wv=$(basename "$whl" | sed -E 's/^[^-]+-([^-]+)-.*/\1/')
  lv=$($PY - <<'EOF'
import tomllib, re, pathlib
d = tomllib.loads(pathlib.Path("pyproject.toml").read_text()); v = d["project"].get("version")
if v is None:
    for init in pathlib.Path(".").glob("*/__init__.py"):
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init.read_text())
        if m: v = m.group(1); break
print(v)
EOF
)
  tc=$($PY -m twine check dist/* 2>&1 | grep -cE "PASSED"); n=$(ls dist | wc -l)
  printf "%-46s local=%-8s wheel=%-8s twine=%s/%s  %s\n" "$r" "$lv" "$wv" "$tc" "$n" "$(basename "$whl")"
done
echo BUILD-ALL-DONE
