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

THE WORKING SET (Model_Cache pruning ruling, user 2026-10-11): an entry is in the
working set when a tracked file in one of the code checkouts NAMES it
(`referenced_by`, derived on every run) or when the keep file pins it with the
user's reason (`kept` — for what no code names: placeholders, a checkpoint a
package fetches by its own hash). Everything else is PRUNABLE; `--prune` lists it
with the sizes and `--prune --yes` deletes it (hub repo dirs, Ollama tags with the
blobs no remaining tag references, flat checkpoints, the derived xet chunk cache).
The manifest written BEFORE a prune is the record of what was there. Cold copies
(scripts/cold_copy.py) ride each entry from the cold ledger.

USAGE
    model_cache_inventory.py [--cache-root DIR] [--out-dir DIR] [--code-root DIR]
                             [--keep-file FILE] [--cold-ledger FILE] [--prune [--yes]]

Writes <out-dir>/model-cache-manifest.json, model-cache-manifest.md and
model-cache-repull.sh. Sizes are apparent bytes (du -sb) per entry.

EXIT CODES
    0  - manifest written (and, with --prune --yes, the prunable entries deleted)
    2  - cache root missing, or the keep file / cold ledger unreadable
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CACHE_ROOT = "/mnt/SN850X_8TB_EXT4/Model_Cache"
CREDENTIAL_FILES = ("huggingface/token", "huggingface/stored_tokens")
DEFAULT_WORKSPACE = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills/cjm-transcription-core"  # the flywheel workspace (training-runs/ + datasets/)
DEFAULT_CODE_ROOT = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills"  # the checkouts whose tracked files define the working set
DEFAULT_PRIVATE_DIR = Path.home() / "cjm-dev-graph-private" / "model-cache"  # cg-backup carries it off-machine
SCAN_SUFFIXES = (".py", ".toml", ".yaml", ".yml", ".json", ".cfg", ".ini", ".sh")
SCAN_MAX_BYTES = 2 * 2**20
SCAN_SKIP_REPOS = ("christianjmills",)  # the website: posts NAME models they once used, which is not a working-set claim


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


def flywheel_entries(workspace: Path) -> List[Dict[str, Any]]:
    """The LOCAL-ONLY class the cache walk cannot see: the data flywheel's training runs
    and extracted datasets under the transcription workspace (DEC 0b3c1044) — the
    irreplaceable subset. Each entry names its latest backup from the artifact's
    lifecycle sidecar (scripts/flywheel_backup.py records it), and `repull` is the
    Hugging Face pull of that path; an artifact with NO backup is flagged `unbacked`.
    The 16 kHz _audio_cache rides as one derived entry (regenerated by the finetune
    adapter from the sources; never pushed)."""
    entries: List[Dict[str, Any]] = []
    for class_dir, kind in (("training-runs", "model"), ("datasets", "dataset")):
        base = workspace / class_dir
        if not base.is_dir():
            continue
        for d in sorted(p for p in base.iterdir() if p.is_dir() and (p / "manifest.json").is_file()):
            try:
                side = json.loads((d / "lifecycle.json").read_text())
            except (OSError, ValueError):
                side = {}
            side = side if isinstance(side, dict) else {}
            backups = [b for b in (side.get("backups") or []) if isinstance(b, dict)]
            hub = [b for b in backups if b.get("target", "huggingface") == "huggingface"]
            last = hub[-1] if hub else None
            entries.append({
                "source": f"flywheel/{class_dir}", "kind": kind, "repo_id": d.name,
                "local_only": True, "lifecycle": side.get("state") or "active",
                "refs": ({str(last.get("repo_id")): str(last.get("revision") or "")} if last else {}),
                "snapshots": [], "incomplete": False, "unbacked": last is None,
                "cold_copies": [{"volume": b.get("repo_id"), "path": b.get("path_in_repo"),
                                 "content_hash": b.get("content_hash"), "at": b.get("at")}
                                for b in backups if b.get("target") == "cold"],
                "size_bytes": _size_bytes(d),
                "repull": ([f"hf download {last.get('repo_id')} --repo-type {last.get('repo_type', kind)} "
                            f"--revision {last.get('revision')} --include '{d.name}/*'"] if last
                           else [f"# NOT BACKED UP: {class_dir}/{d.name} — run scripts/flywheel_backup.py"]),
            })
    cache = workspace / "training-runs" / "_audio_cache"
    if cache.is_dir():
        entries.append({"source": "flywheel/training-runs", "kind": "derived", "repo_id": "_audio_cache",
                        "local_only": False, "refs": {}, "snapshots": [], "incomplete": False,
                        "unbacked": False, "size_bytes": _size_bytes(cache),
                        "repull": ["# derived: the 16 kHz mono transcode of the sources, keyed by "
                                   "content hash; the finetune adapter regenerates it"]})
    return entries


def entry_key(e: Dict[str, Any]) -> str:
    """`<source>:<repo_id>` — the one handle the keep file, the cold ledger and the
    cold_copy CLI name an entry by."""
    return f"{e['source']}:{e['repo_id']}"


def entry_needles(e: Dict[str, Any]) -> List[str]:
    """The strings whose presence in a tracked file means the code NAMES this entry: a hub
    repo id, an Ollama `model:tag`, a flat checkpoint's file name; a whisper size only
    QUOTED (a bare `base` is every other word) and only in a file that says whisper
    (see `scan_references`). Derived stores (blobs, the audio cache) and the flywheel's
    own artifacts have none — they are never prunable."""
    src = e["source"]
    if src in ("huggingface", "gpt4all", "torch-hub") or (src == "ollama" and e["kind"] == "model"):
        return [e["repo_id"]]
    if src == "whisper":
        return [f'"{e["repo_id"]}"', f"'{e['repo_id']}'"]
    return []


def _tracked_files(repo: Path) -> List[Path]:
    """A checkout's tracked text files of the scanned kinds (`git ls-files`: caches,
    runtimes and build output never count as a reference)."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True,
                             check=True).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.CalledProcessError):
        return []
    return [repo / p for p in out.split("\0") if p.endswith(SCAN_SUFFIXES)]


def scan_references(entries: List[Dict[str, Any]], code_root: Path) -> None:
    """Annotate each entry with `referenced_by`: every `<repo>/<path>` under the code root
    whose tracked text names it (entry_needles) — the working set read off the code,
    never remembered. A whisper needle counts only in a file that mentions whisper."""
    wanted = [(e, entry_needles(e)) for e in entries]
    for e, needles in wanted:
        if needles:
            e["referenced_by"] = []
    if not code_root.is_dir():
        return
    for repo in sorted(p for p in code_root.iterdir() if (p / ".git").exists()):
        if repo.name in SCAN_SKIP_REPOS:
            continue
        for f in _tracked_files(repo):
            try:
                if f.stat().st_size > SCAN_MAX_BYTES:
                    continue
                text = f.read_text(errors="replace")
            except OSError:
                continue
            says_whisper = "whisper" in text.lower()
            for e, needles in wanted:
                if needles and (e["source"] != "whisper" or says_whisper) \
                        and any(n in text for n in needles):
                    e["referenced_by"].append(f"{repo.name}/{f.relative_to(repo).as_posix()}")


def load_json_file(path: Optional[Path], default: Any) -> Any:
    """A JSON side file (the keep file, the cold ledger), or `default` when it does not
    exist yet; an unreadable file raises — a silent default would prune what it pins."""
    if path is None or not path.exists():
        return default
    return json.loads(path.read_text())


def annotate_working_set(entries: List[Dict[str, Any]], keep: Dict[str, Any],
                         ledger: Dict[str, Any]) -> None:
    """Fold the keep pins (`kept` = the user's reason) and the cold ledger's copies onto the
    entries, then derive `prunable`: an entry with needles that nothing references and
    nothing pins. A cold copy never keeps an entry in the cache — it is what makes the
    deletion cost bandwidth or nothing, not knowledge."""
    pins = {p["key"]: p for p in keep.get("pins", []) if isinstance(p, dict) and p.get("key")}
    copies: Dict[str, List[Dict[str, Any]]] = {}
    for c in ledger.get("copies", []):
        if isinstance(c, dict) and c.get("key"):
            copies.setdefault(c["key"], []).append(c)
    for e in entries:
        k = entry_key(e)
        if k in pins:
            e["kept"] = pins[k].get("reason", "")
        if k in copies:
            e["cold_copies"] = (e.get("cold_copies") or []) + copies[k]
        e["prunable"] = bool(entry_needles(e)) and not e.get("referenced_by") and k not in pins


def build_manifest(root: Path, workspace: Path = None, code_root: Path = None,
                   keep: Dict[str, Any] = None, ledger: Dict[str, Any] = None) -> Dict[str, Any]:
    """The whole manifest: every cache entry across the stores, the flywheel's local-only
    artifacts (when a workspace is given) with their backups, the working set (code
    references + keep pins, when a code root is given) and its cold copies, the credential
    files noted as present-but-excluded, and the totals — `unbacked` lists every
    local-only artifact nothing has pushed yet (the gap a drive loss would turn into a
    real loss), `prunable` every entry outside the working set."""
    entries = (hub_entries(root / "huggingface" / "hub") + ollama_entries(root / "Ollama")
               + flat_entries(root))
    if workspace is not None:
        entries += flywheel_entries(workspace)
    if code_root is not None:
        scan_references(entries, code_root)
        annotate_working_set(entries, keep or {}, ledger or {})
    creds = [c for c in CREDENTIAL_FILES if (root / c).exists()]
    return {"cache_root": str(root), "workspace": (str(workspace) if workspace else None),
            "code_root": (str(code_root) if code_root else None),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "entries": entries,
            "credentials_present_excluded": creds,
            "xet_chunk_cache_bytes": _size_bytes(root / "huggingface" / "xet"),
            "total_bytes": sum(e["size_bytes"] for e in entries),
            "incomplete": [e["repo_id"] for e in entries if e.get("incomplete")],
            "local_only": [e["repo_id"] for e in entries if e.get("local_only")],
            "unbacked": [e["repo_id"] for e in entries if e.get("unbacked")],
            "prunable": [entry_key(e) for e in entries if e.get("prunable")]}


def _ollama_referenced_blobs(ollama_dir: Path, exclude: set = frozenset()) -> set:
    """Every blob file name (`sha256-<hex>`) an Ollama manifest names (its config and
    layers), skipping the manifests in `exclude` — the blobs a prune must keep are the
    ones the SURVIVING tags name."""
    keep = set()
    for m in (ollama_dir / "manifests").rglob("*") if (ollama_dir / "manifests").is_dir() else []:
        if not m.is_file() or m in exclude:
            continue
        try:
            doc = json.loads(m.read_text())
        except (OSError, ValueError):
            continue
        for layer in [doc.get("config") or {}] + list(doc.get("layers") or []):
            if isinstance(layer, dict) and layer.get("digest"):
                keep.add(str(layer["digest"]).replace(":", "-"))
    return keep


def _ollama_tag_file(ollama_dir: Path, e: Dict[str, Any]) -> Path:
    """The manifest file of an Ollama `model:tag` entry (namespace `library` when bare)."""
    name, _, tag = e["repo_id"].rpartition(":")
    ns, _, model = name.rpartition("/")
    return ollama_dir / "manifests" / e.get("registry", "registry.ollama.ai") / (ns or "library") / model / tag


def prune_targets(root: Path, manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What a prune removes, path by path: each prunable entry's on-disk path (a hub repo
    dir and its lock dir, an Ollama tag's manifest, a flat checkpoint), then the Ollama
    blobs no surviving tag references, then the derived xet chunk cache."""
    hub = root / "huggingface" / "hub"
    out: List[Dict[str, Any]] = []
    for e in manifest["entries"]:
        if not e.get("prunable"):
            continue
        src = e["source"]
        if src == "huggingface":
            d = hub / f"{e['kind']}s--{e['repo_id'].replace('/', '--')}"
            paths = [d, hub / ".locks" / d.name]
        elif src == "ollama":
            paths = [_ollama_tag_file(root / "Ollama", e)]
        else:
            sub = {"whisper": "whisper", "gpt4all": "gpt4all", "torch-hub": "torch/hub/checkpoints"}[src]
            paths = [root / sub / (e["repo_id"] + (".pt" if src == "whisper" else ""))]
        out.append({"key": entry_key(e), "paths": [p for p in paths if p.exists()],
                    "size_bytes": e["size_bytes"]})
    return out


def prune(root: Path, manifest: Dict[str, Any], yes: bool = False) -> Dict[str, Any]:
    """List (and with `yes`, delete) everything outside the working set. The Ollama blobs
    to drop are those no SURVIVING tag names (computed before anything is removed, so the
    dry run and the deletion agree); the xet chunk cache is derived and always goes.
    Returns the targets and the bytes freed."""
    targets = prune_targets(root, manifest)
    ollama = root / "Ollama"
    pruned = {p for t in targets for p in t["paths"]}
    survivors = _ollama_referenced_blobs(ollama, exclude=pruned)
    blobs = ([b for b in (ollama / "blobs").iterdir() if b.is_file() and b.name not in survivors]
             if (ollama / "blobs").is_dir() else [])
    blob_bytes = sum(b.stat().st_size for b in blobs)
    xet = root / "huggingface" / "xet"
    xet_bytes = manifest.get("xet_chunk_cache_bytes", 0) if xet.is_dir() else 0
    if yes:
        for p in sorted(pruned):
            shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
        for b in blobs:
            b.unlink()
        if xet.is_dir():
            shutil.rmtree(xet)
    return {"targets": targets, "ollama_blobs": len(blobs), "ollama_blob_bytes": blob_bytes,
            "xet_bytes": xet_bytes,
            "freed_bytes": sum(t["size_bytes"] for t in targets) + blob_bytes + xet_bytes,
            "deleted": yes}


def _standing(e: Dict[str, Any]) -> str:
    """An entry's working-set cell: the code that names it, the keep pin's reason, PRUNABLE,
    or a dash for what is never prunable (derived stores, the flywheel's artifacts)."""
    refs = e.get("referenced_by") or []
    if refs:
        return f"named by {refs[0]}" + (f" (+{len(refs) - 1})" if len(refs) > 1 else "")
    if e.get("kept") is not None:
        return f"kept: {e['kept']}"
    return "**PRUNABLE**" if e.get("prunable") else "—"


def render_markdown(manifest: Dict[str, Any]) -> str:
    """The manifest as a size-sorted table (largest first) with the totals and caveats;
    local-only rows show their backup in the revision column and flag a missing one; the
    working-set column says why an entry stays (or that it is prunable), the cold column
    names its verified cold copies."""
    gib = lambda b: f"{b / 2**30:.1f} GiB" if b >= 2**30 else f"{b / 2**20:.1f} MiB"
    scanned = manifest.get("code_root") is not None
    lines = [f"# Model_Cache inventory — {manifest['generated_at']}", "",
             f"Root `{manifest['cache_root']}`"
             + (f" + workspace `{manifest['workspace']}`" if manifest.get("workspace") else "")
             + f" · {len(manifest['entries'])} entries · "
             f"{gib(manifest['total_bytes'])} (+ {gib(manifest['xet_chunk_cache_bytes'])} xet chunk cache)", ""]
    if scanned:
        lines += [f"Working set read off the tracked files under `{manifest['code_root']}` plus the keep "
                  f"pins; prunable: {len(manifest.get('prunable') or [])} entries, "
                  f"{gib(sum(e['size_bytes'] for e in manifest['entries'] if e.get('prunable')))}.", "",
                  "| source | entry | revision(s) / backup | size | working set | cold copy |",
                  "|---|---|---|---|---|---|"]
    else:
        lines += ["| source | entry | revision(s) / backup | size |", "|---|---|---|---|"]
    for e in sorted(manifest["entries"], key=lambda e: -e["size_bytes"]):
        revs = (", ".join(f"{k}={v[:10]}" for k, v in e["refs"].items())
                or ", ".join(s[:10] for s in e["snapshots"]) or "—")
        flag = (" ⚠ incomplete" if e.get("incomplete") else "") + \
               (" ⚠ NOT BACKED UP" if e.get("unbacked") else "") + \
               (" (local-only)" if e.get("local_only") else "")
        row = f"| {e['source']} | `{e['repo_id']}`{flag} | {revs} | {gib(e['size_bytes'])} |"
        if scanned:
            cold = ", ".join(f"{c.get('volume')}:{c.get('path')}" for c in e.get("cold_copies") or []) or "—"
            row += f" {_standing(e)} | {cold} |"
        lines.append(row)
    lines += ["", f"Credential files present, EXCLUDED from this manifest: "
                  f"{', '.join(manifest['credentials_present_excluded']) or 'none'}.",
              f"Local-only artifacts: {len(manifest.get('local_only') or [])} "
              f"(not backed up: {len(manifest.get('unbacked') or [])}).",
              "Every hub entry is re-downloadable from its hub (see model-cache-repull.sh); gated repos "
              "need the account's access, community merges may be withdrawn upstream; local-only "
              "artifacts re-pull from their private backup repos."]
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
    """CLI: inventory the cache root (+ the flywheel workspace, + the working set) into the
    three manifest files; with --prune, list what is outside the working set, and with
    --prune --yes delete it and write the manifest again."""
    ap = argparse.ArgumentParser(description="Inventory the shared Model_Cache (+ the flywheel's local-only artifacts) as a re-pull manifest.")
    ap.add_argument("--cache-root", default=DEFAULT_CACHE_ROOT)
    ap.add_argument("--workspace", default=DEFAULT_WORKSPACE,
                    help="the transcription workspace holding training-runs/ + datasets/ ('' to skip)")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--code-root", default=DEFAULT_CODE_ROOT,
                    help="the checkouts whose tracked files define the working set ('' to skip the scan)")
    ap.add_argument("--keep-file", default=str(DEFAULT_PRIVATE_DIR / "keep.json"),
                    help="the user's keep pins: {\"pins\": [{\"key\": \"<source>:<repo_id>\", \"reason\": ...}]}")
    ap.add_argument("--cold-ledger", default=str(DEFAULT_PRIVATE_DIR / "cold-copies.json"),
                    help="the verified cold copies scripts/cold_copy.py records")
    ap.add_argument("--prune", action="store_true", help="list every entry outside the working set")
    ap.add_argument("--yes", action="store_true", help="with --prune: delete them")
    args = ap.parse_args(argv)
    root = Path(args.cache_root)
    if not root.is_dir():
        print(f"cache root missing: {root}", file=sys.stderr)
        return 2
    if args.prune and not args.code_root:
        print("--prune needs the working set: give --code-root", file=sys.stderr)
        return 2
    try:
        keep = load_json_file(Path(args.keep_file) if args.keep_file else None, {})
        ledger = load_json_file(Path(args.cold_ledger) if args.cold_ledger else None, {})
    except (OSError, ValueError) as e:
        print(f"unreadable keep file / cold ledger: {e}", file=sys.stderr)
        return 2
    ws = Path(args.workspace) if args.workspace else None
    code_root = Path(args.code_root) if args.code_root else None
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    def inventory() -> Dict[str, Any]:
        m = build_manifest(root, workspace=ws if (ws and ws.is_dir()) else None,
                           code_root=code_root, keep=keep, ledger=ledger)
        (out / "model-cache-manifest.json").write_text(json.dumps(m, indent=2) + "\n")
        (out / "model-cache-manifest.md").write_text(render_markdown(m))
        (out / "model-cache-repull.sh").write_text(render_repull(m))
        print(f"{len(m['entries'])} entries · {m['total_bytes'] / 2**30:.1f} GiB · "
              f"incomplete: {len(m['incomplete'])} · local-only: {len(m['local_only'])} "
              f"(not backed up: {len(m['unbacked'])}) · prunable: {len(m['prunable'])} · written to {out}")
        return m

    manifest = inventory()
    if not args.prune:
        return 0
    result = prune(root, manifest, yes=args.yes)
    for t in sorted(result["targets"], key=lambda t: -t["size_bytes"]):
        print(f"{'-' if args.yes else '~'} {t['key']}  {t['size_bytes'] / 2**30:.1f} GiB")
    print(f"{'deleted' if args.yes else 'would delete'}: {len(result['targets'])} entries + "
          f"{result['ollama_blobs']} unreferenced Ollama blob(s) ({result['ollama_blob_bytes'] / 2**30:.1f} GiB) + "
          f"the xet chunk cache ({result['xet_bytes'] / 2**30:.1f} GiB) = {result['freed_bytes'] / 2**30:.1f} GiB")
    if args.yes:
        inventory()
    return 0


if __name__ == "__main__":
    sys.exit(main())
