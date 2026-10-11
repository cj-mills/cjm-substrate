#!/usr/bin/env python3
"""
cold_copy.py

Copy chosen Model_Cache entries and the data flywheel's local-only artifacts to a
COLD volume (low-access ext4 storage — the BX500 drives, Model_Cache pruning ruling,
user 2026-10-11) as plain trees, symlinks kept (a hub snapshot is a tree of links into
its blobs), VERIFY each copy by a tree hash taken over the source and over the copy
(sha256 per regular file, the link text per symlink, sorted by relative path), and
record what landed: a cache entry in the cold ledger (beside the inventory, which
cg-backup carries off-machine, and mirrored onto the volume itself so the drive
describes its own contents), a flywheel artifact on its lifecycle sidecar
(record_backup, target "cold") — the second copy beside its private Hugging Face push
(0b3c1044's open second-copy question, answered by the same ruling). A copy is
recorded only once it verifies; re-runs skip an entry the ledger already records on
this volume at the source's current tree hash.

Entries are named by the inventory's key, `<source>:<repo_id>` (see
scripts/model_cache_inventory.py: `huggingface:pyannote/segmentation-3.0`,
`whisper:large-v3`, `torch-hub:<file>`).

Run it as a script (`python scripts/cold_copy.py …`): Python puts the script's own
directory first on sys.path, which is how its siblings flywheel_backup (the artifact
discovery + the sidecar loader) and model_cache_inventory (the entry list + keys)
import.

USAGE
    cold_copy.py --cold-root DIR [--entry KEY ...] [--flywheel] [--ledger FILE]
                 [--cache-root DIR] [--workspace DIR] [--dry-run]

EXIT CODES
    0  - every requested copy landed and verified (or already was)
    1  - a copy failed to verify (nothing is recorded for it)
    2  - configuration error (cold root missing or not a mount's filesystem we can
         name, unknown entry key, unreadable ledger)
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import flywheel_backup
import model_cache_inventory as inventory

TARGET = "cold"


def volume_identity(cold_root: Path) -> Tuple[str, str]:
    """(label, uuid) of the filesystem holding the cold root (`findmnt -T`) — a copy is
    recorded against the VOLUME, never a mount path that can move."""
    out = subprocess.run(["findmnt", "-no", "LABEL,UUID", "-T", str(cold_root)],
                         capture_output=True, text=True, check=True).stdout.split()
    if len(out) == 1:
        return "", out[0]
    if len(out) < 2:
        raise RuntimeError(f"cannot name the filesystem under {cold_root}")
    return out[0], out[1]


def _sha256(path: Path) -> str:
    """Hex sha256 over a file's bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(root: Path) -> Tuple[str, int, int]:
    """(`sha256:<hex>`, file count, bytes) over a tree: each regular file as its relative
    path + content sha256, each symlink as its relative path + link text (never
    followed), sorted by path — equal for a source and a faithful copy."""
    rows: List[str] = []
    files = size = 0
    if root.is_file():
        return "sha256:" + hashlib.sha256(f"F\0{_sha256(root)}\n".encode()).hexdigest(), 1, root.stat().st_size
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames) + sorted(d for d in dirnames if (Path(dirpath) / d).is_symlink()):
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                rows.append(f"L\0{rel}\0{os.readlink(p)}")
            elif p.is_file():
                rows.append(f"F\0{rel}\0{_sha256(p)}")
                files += 1
                size += p.stat().st_size
    digest = hashlib.sha256("\n".join(sorted(rows)).encode("utf-8")).hexdigest()
    return "sha256:" + digest, files, size


def copy_tree(src: Path, dest: Path, exclude: Tuple[str, ...] = ()) -> None:
    """rsync the source to the destination, symlinks as symlinks (`-a`); a directory's
    trailing slash copies its contents into dest, a file lands as dest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    flags = ["rsync", "-a"] + [f"--exclude={x}" for x in exclude]
    subprocess.run(flags + ([f"{src}/", f"{dest}/"] if src.is_dir() else [str(src), str(dest)]), check=True)


def cache_entry_path(cache_root: Path, e: Dict[str, Any]) -> Path:
    """Where an inventory entry lives in the cache (its whole hub repo dir, or the file)."""
    src = e["source"]
    if src == "huggingface":
        return cache_root / "huggingface" / "hub" / f"{e['kind']}s--{e['repo_id'].replace('/', '--')}"
    sub = {"whisper": "whisper", "gpt4all": "gpt4all", "torch-hub": "torch/hub/checkpoints"}.get(src)
    if sub is None:
        raise ValueError(f"{inventory.entry_key(e)}: only hub repos and flat checkpoints copy cold")
    return cache_root / sub / (e["repo_id"] + (".pt" if src == "whisper" else ""))


def load_ledger(path: Path) -> Dict[str, Any]:
    """The cold ledger ({"copies": [...]}); absent = empty, unreadable raises."""
    return inventory.load_json_file(path, {"copies": []})


def ledger_has(ledger: Dict[str, Any], key: str, uuid: str, digest: str) -> Optional[Dict[str, Any]]:
    """The ledger's record of this entry on this volume at this tree hash, if any."""
    for c in ledger.get("copies", []):
        if c.get("key") == key and c.get("volume_uuid") == uuid and c.get("tree_hash") == digest:
            return c
    return None


def write_ledger(ledger: Dict[str, Any], path: Path, cold_root: Path) -> None:
    """The ledger beside the inventory AND on the volume (the drive names its contents)."""
    text = json.dumps(ledger, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    (cold_root / "cold-copies.json").write_text(text)


def copy_and_verify(src: Path, dest: Path) -> Tuple[bool, str, int, int]:
    """Copy, then hash source and copy; (verified, source tree hash, files, bytes)."""
    copy_tree(src, dest)
    want, files, size = tree_hash(src)
    got, _, _ = tree_hash(dest)
    return want == got, want, files, size


def main(argv: List[str] = None) -> int:
    """CLI: copy the named cache entries (+ the flywheel's artifacts) cold, verify, record."""
    ap = argparse.ArgumentParser(description="Copy Model_Cache entries and flywheel artifacts to a cold volume, verified.")
    ap.add_argument("--cold-root", required=True, help="the cold tree's root on the cold volume")
    ap.add_argument("--entry", action="append", default=[], help="an inventory key <source>:<repo_id> (repeatable)")
    ap.add_argument("--flywheel", action="store_true", help="also copy every flywheel training run + dataset")
    ap.add_argument("--ledger", default=str(inventory.DEFAULT_PRIVATE_DIR / "cold-copies.json"))
    ap.add_argument("--cache-root", default=inventory.DEFAULT_CACHE_ROOT)
    ap.add_argument("--workspace", default=inventory.DEFAULT_WORKSPACE)
    ap.add_argument("--dry-run", action="store_true", help="list what would be copied; touch nothing")
    args = ap.parse_args(argv)
    cold_root, cache_root = Path(args.cold_root), Path(args.cache_root)
    if not cold_root.is_dir():
        print(f"cold root missing: {cold_root}", file=sys.stderr)
        return 2
    try:
        label, uuid = volume_identity(cold_root)
        ledger = load_ledger(Path(args.ledger))
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return 2
    volume = label or uuid
    by_key = {inventory.entry_key(e): e for e in
              inventory.hub_entries(cache_root / "huggingface" / "hub") + inventory.flat_entries(cache_root)}
    unknown = [k for k in args.entry if k not in by_key]
    if unknown:
        print(f"unknown entry key(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    copied = skipped = failed = 0
    for key in args.entry:
        src = cache_entry_path(cache_root, by_key[key])
        rel = Path("model-cache") / src.relative_to(cache_root)
        digest = tree_hash(src)[0]
        if ledger_has(ledger, key, uuid, digest):
            skipped += 1
            print(f"= {key}  already on {volume}:{rel}")
            continue
        if args.dry_run:
            print(f"~ {key}  would copy -> {volume}:{rel}")
            continue
        ok, digest, files, size = copy_and_verify(src, cold_root / rel)
        if not ok:
            failed += 1
            print(f"! {key}  copied to {rel} but the tree hash DIFFERS — not recorded")
            continue
        ledger.setdefault("copies", []).append({
            "key": key, "volume": volume, "volume_uuid": uuid, "path": rel.as_posix(),
            "tree_hash": digest, "files": files, "size_bytes": size,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        write_ledger(ledger, Path(args.ledger), cold_root)
        copied += 1
        print(f"+ {key}  -> {volume}:{rel}  ({files} file(s), {size / 2**20:.1f} MiB, verified)")
    if args.flywheel:
        for a in flywheel_backup.discover_artifacts(Path(args.workspace)):
            rel = Path("flywheel") / a["class"] / a["id"]
            life = flywheel_backup._lifecycle(a["dir"])
            if any(b.get("target") == TARGET and b.get("repo_id") == f"{volume}:{uuid}"
                   and b.get("content_hash") == a["content_hash"] for b in life.backups):
                skipped += 1
                print(f"= {a['id']}  already on {volume}:{rel}")
                continue
            if args.dry_run:
                print(f"~ {a['id']}  would copy -> {volume}:{rel}")
                continue
            dest = cold_root / rel
            copy_tree(a["dir"], dest, exclude=("lifecycle.json",))  # the sidecar stays with the artifact, as on the hub push
            problems = [r for r, d in a["files"].items()
                        if not (dest / r).is_file() or "sha256:" + _sha256(dest / r) != d]
            if problems:
                failed += 1
                print(f"! {a['id']}  copied but NOT verified: {', '.join(problems[:5])}")
                continue
            life.record_backup(target=TARGET, repo_id=f"{volume}:{uuid}", repo_type=a["kind"],
                               path_in_repo=rel.as_posix(), revision=a["content_hash"],
                               content_hash=a["content_hash"])
            copied += 1
            print(f"+ {a['id']}  -> {volume}:{rel}  ({len(a['files'])} file(s), verified)")
    print(f"{copied} copied, {skipped} already cold, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
