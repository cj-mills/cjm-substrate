#!/usr/bin/env python3
"""
model_cache_inventory.py

Inventory the shared Model_Cache (the SOLE copy of every cached model since the
990pro image was deleted — work item f2b424c8) as a re-pull MANIFEST: what is in
the cache, where it came from and at which revision, so a drive failure costs
bandwidth, not knowledge. The cache holds published weights (Hugging Face hub
snapshots, Ollama library models, OpenAI whisper checkpoints, a gpt4all gguf, a
torch hub checkpoint) — every entry is re-downloadable from its hub, so the
durable artifact is this manifest plus a generated re-pull script, not a 585 GB
mirror. Credential files (huggingface/token, stored_tokens) are reported as
present and NEVER copied into the manifest.

USAGE
    model_cache_inventory.py [--cache-root DIR] [--out-dir DIR]

Writes <out-dir>/model-cache-manifest.json, model-cache-manifest.md and
model-cache-repull.sh. Sizes are apparent bytes (du -sb) per entry.

EXIT CODES
    0  - manifest written
    2  - cache root missing
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_CACHE_ROOT = "/mnt/SN850X_8TB_EXT4/Model_Cache"
CREDENTIAL_FILES = ("huggingface/token", "huggingface/stored_tokens")


def _size_bytes(path: Path) -> int:
    """Apparent size of a file or tree in bytes (`du -sb`; 0 when du is unavailable)."""
    try:
        out = subprocess.run(["du", "-sb", str(path)], capture_output=True, text=True, check=True)
        return int(out.stdout.split()[0])
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
        return 0


def hub_entries(hub_dir: Path) -> List[Dict[str, Any]]:
    """One entry per Hugging Face hub repo cache dir (`models--org--name` / `datasets--…`):
    repo id, kind, the refs (branch -> commit sha), the snapshot shas present, whether any
    download is incomplete, and the size. The re-pull is `hf download <repo> --revision <sha>`."""
    entries: List[Dict[str, Any]] = []
    if not hub_dir.is_dir():
        return entries
    for d in sorted(hub_dir.iterdir()):
        if not d.is_dir() or "--" not in d.name:
            continue
        kind, _, rest = d.name.partition("--")
        repo_id = rest.replace("--", "/")
        refs = {}
        for r in sorted((d / "refs").glob("*")) if (d / "refs").is_dir() else []:
            try:
                refs[r.name] = r.read_text().strip()
            except OSError:
                pass
        snapshots = sorted(p.name for p in (d / "snapshots").iterdir()) if (d / "snapshots").is_dir() else []
        incomplete = any((d / "blobs").glob("*.incomplete")) if (d / "blobs").is_dir() else False
        entries.append({"source": "huggingface", "kind": kind.rstrip("s"), "repo_id": repo_id,
                        "refs": refs, "snapshots": snapshots, "incomplete": incomplete,
                        "size_bytes": _size_bytes(d),
                        "repull": [f"hf download {repo_id} --revision {sha}"
                                   + (" --repo-type dataset" if kind == "datasets" else "")
                                   for sha in (refs.values() or snapshots)]})
    return entries


def ollama_entries(ollama_dir: Path) -> List[Dict[str, Any]]:
    """One entry per Ollama model manifest (`manifests/<registry>/<ns>/<model>/<tag>`); the
    blobs store is shared, so the size rides one summary entry. Re-pull = `ollama pull model:tag`."""
    entries: List[Dict[str, Any]] = []
    manifests = ollama_dir / "manifests"
    if not manifests.is_dir():
        return entries
    for tag_file in sorted(p for p in manifests.rglob("*") if p.is_file()):
        rel = tag_file.relative_to(manifests).parts  # registry, namespace, model, tag
        if len(rel) < 4:
            continue
        registry, namespace, model, tag = rel[0], rel[1], rel[2], rel[3]
        name = f"{model}:{tag}" if namespace == "library" else f"{namespace}/{model}:{tag}"
        entries.append({"source": "ollama", "kind": "model", "repo_id": name, "registry": registry,
                        "refs": {}, "snapshots": [], "incomplete": False, "size_bytes": 0,
                        "repull": [f"ollama pull {name}"]})
    entries.append({"source": "ollama", "kind": "blobs", "repo_id": "(shared blob store)",
                    "refs": {}, "snapshots": [], "incomplete": False,
                    "size_bytes": _size_bytes(ollama_dir / "blobs"), "repull": []})
    return entries


def flat_entries(root: Path) -> List[Dict[str, Any]]:
    """The flat checkpoint stores: whisper `<name>.pt` (re-pull by name through the whisper
    package), gpt4all `.gguf` (re-download from the gpt4all model list), torch hub
    checkpoints (re-fetched by whatever loads them)."""
    entries: List[Dict[str, Any]] = []
    for f in sorted((root / "whisper").glob("*.pt")) if (root / "whisper").is_dir() else []:
        entries.append({"source": "whisper", "kind": "checkpoint", "repo_id": f.stem,
                        "refs": {}, "snapshots": [], "incomplete": False,
                        "size_bytes": _size_bytes(f),
                        "repull": [f"python -c \"import whisper; whisper.load_model('{f.stem}', download_root='{root / 'whisper'}')\""]})
    for f in sorted((root / "gpt4all").glob("*.gguf")) if (root / "gpt4all").is_dir() else []:
        entries.append({"source": "gpt4all", "kind": "gguf", "repo_id": f.name,
                        "refs": {}, "snapshots": [], "incomplete": False,
                        "size_bytes": _size_bytes(f), "repull": [f"# gpt4all: re-download {f.name} from the model list"]})
    tc = root / "torch" / "hub" / "checkpoints"
    for f in sorted(tc.iterdir()) if tc.is_dir() else []:
        entries.append({"source": "torch-hub", "kind": "checkpoint", "repo_id": f.name,
                        "refs": {}, "snapshots": [], "incomplete": False,
                        "size_bytes": _size_bytes(f), "repull": [f"# torch hub: re-fetched on first load ({f.name})"]})
    return entries


def build_manifest(root: Path) -> Dict[str, Any]:
    """The whole-cache manifest: every entry across the stores, the credential files noted
    as present-but-excluded, and the totals."""
    entries = (hub_entries(root / "huggingface" / "hub") + ollama_entries(root / "Ollama")
               + flat_entries(root))
    creds = [c for c in CREDENTIAL_FILES if (root / c).exists()]
    return {"cache_root": str(root), "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "entries": entries,
            "credentials_present_excluded": creds,
            "xet_chunk_cache_bytes": _size_bytes(root / "huggingface" / "xet"),
            "total_bytes": sum(e["size_bytes"] for e in entries),
            "incomplete": [e["repo_id"] for e in entries if e.get("incomplete")]}


def render_markdown(manifest: Dict[str, Any]) -> str:
    """The manifest as a size-sorted table (largest first) with the totals and caveats."""
    gib = lambda b: f"{b / 2**30:.1f} GiB"
    lines = [f"# Model_Cache inventory — {manifest['generated_at']}", "",
             f"Root `{manifest['cache_root']}` · {len(manifest['entries'])} entries · "
             f"{gib(manifest['total_bytes'])} (+ {gib(manifest['xet_chunk_cache_bytes'])} xet chunk cache)", "",
             "| source | entry | revision(s) | size |", "|---|---|---|---|"]
    for e in sorted(manifest["entries"], key=lambda e: -e["size_bytes"]):
        revs = ", ".join(f"{k}={v[:10]}" for k, v in e["refs"].items()) or ", ".join(s[:10] for s in e["snapshots"]) or "—"
        flag = " ⚠ incomplete" if e.get("incomplete") else ""
        lines.append(f"| {e['source']} | `{e['repo_id']}`{flag} | {revs} | {gib(e['size_bytes'])} |")
    lines += ["", f"Credential files present, EXCLUDED from this manifest: "
                  f"{', '.join(manifest['credentials_present_excluded']) or 'none'}.",
              "Every entry is re-downloadable from its hub (see model-cache-repull.sh); gated repos "
              "need the account's access, community merges may be withdrawn upstream."]
    return "\n".join(lines) + "\n"


def render_repull(manifest: Dict[str, Any]) -> str:
    """A shell script that re-pulls every entry at its recorded revision (HF_HOME must point
    at the cache root's huggingface dir; Ollama's models dir at Ollama/)."""
    lines = ["#!/usr/bin/env bash", "# Re-pull the Model_Cache from its hubs — generated by model_cache_inventory.py",
             f"# generated {manifest['generated_at']} from {manifest['cache_root']}",
             "set -euo pipefail",
             f"export HF_HOME={manifest['cache_root']}/huggingface",
             f"export OLLAMA_MODELS={manifest['cache_root']}/Ollama", ""]
    for e in manifest["entries"]:
        for cmd in e.get("repull") or []:
            lines.append(cmd)
    return "\n".join(lines) + "\n"


def main(argv: List[str] = None) -> int:
    """CLI: inventory the cache root into the three manifest files."""
    ap = argparse.ArgumentParser(description="Inventory the shared Model_Cache as a re-pull manifest.")
    ap.add_argument("--cache-root", default=DEFAULT_CACHE_ROOT)
    ap.add_argument("--out-dir", default=".")
    args = ap.parse_args(argv)
    root = Path(args.cache_root)
    if not root.is_dir():
        print(f"cache root missing: {root}", file=sys.stderr)
        return 2
    manifest = build_manifest(root)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "model-cache-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out / "model-cache-manifest.md").write_text(render_markdown(manifest))
    (out / "model-cache-repull.sh").write_text(render_repull(manifest))
    print(f"{len(manifest['entries'])} entries · {manifest['total_bytes'] / 2**30:.1f} GiB · "
          f"incomplete: {len(manifest['incomplete'])} · written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
