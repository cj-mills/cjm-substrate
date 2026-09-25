"""Interim dev-mode refresh of every capability worker env on this machine (the recipe env
truth prints, applied to the WHOLE cjm-* set in the env, not just the capability): for each
workspace manifest, editable-install from the checkout, --no-deps, every cjm-* dist the env
already holds whose checkout exists under the repos root; then `pip check` reports any
dependency the --no-deps refresh left unmet. Journal: one line per env."""

import json
import subprocess
from pathlib import Path

ROOT = Path("/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills")
WORKSPACES = ["cjm-transcription-core", "cjm-transcript-decomp-core", "cjm-transcript-correction-core"]


def run(py, *args):
    return subprocess.run([str(py), "-m", "pip", *args], capture_output=True, text=True)


seen = set()
for ws in WORKSPACES:
    for mf in sorted((ROOT / ws / ".cjm" / "manifests").glob("cjm-capability-*.json")):
        d = json.loads(mf.read_text())
        py = Path(d["install"]["python_path"])
        if not py.exists() or py in seen:
            continue
        seen.add(py)
        env = py.parent.parent.name
        r = run(py, "list", "--format", "json")
        dists = [p["name"] for p in json.loads(r.stdout) if p["name"].startswith("cjm-")]
        refreshed, missing = [], []
        for name in dists:
            src = ROOT / name
            if (src / "pyproject.toml").is_file():
                rr = run(py, "install", "-e", str(src), "--no-deps", "-q")
                refreshed.append(name if rr.returncode == 0 else f"{name}!FAILED")
            else:
                missing.append(name)
        chk = run(py, "check")
        after = {p["name"]: p["version"] for p in json.loads(run(py, "list", "--format", "json").stdout) if p["name"].startswith("cjm-")}
        print(f"[{ws}] {env}")
        print(f"   refreshed: {' '.join(refreshed)}")
        if missing:
            print(f"   no checkout (left as installed): {' '.join(missing)}")
        print(f"   now: {' '.join(f'{k}={v}' for k, v in sorted(after.items()))}")
        print(f"   pip check: {'OK' if chk.returncode == 0 else chk.stdout.strip().replace(chr(10), ' | ')[:400]}")
print("REFRESH-DONE")
