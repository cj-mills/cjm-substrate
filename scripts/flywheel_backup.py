#!/usr/bin/env python3
"""
flywheel_backup.py

Push the data flywheel's LOCAL-ONLY artifacts — the finetuned training runs
(training-runs/<run_id>/: model.ckpt + manifest + logs) and the extracted datasets
(datasets/<dataset_id>/: events/regions jsonl + manifest) — to PRIVATE repos on the
user's Hugging Face account, the backup target ruled in DEC 0b3c1044 (off-the-shelf
weights re-download from their hubs; only what the flywheel produced is
irreplaceable). One model repo holds every run under <run_id>/, one dataset repo
every dataset under <dataset_id>/ — the provenance ids the manifests carry ARE the
paths, so a fresh pull is `hf download <repo> --include '<id>/*'`. Every push is
verified by a fresh download compared file-for-file by sha256, then recorded on the
artifact's lifecycle sidecar (ArtifactLifecycle.record_backup) so the backup is
discoverable from the artifact, never from memory. Re-runs are idempotent: an
artifact whose sidecar already records this repo at the current content hash is
skipped. The 16 kHz _audio_cache is a derived transcode and is never pushed.

USAGE (the cjm-substrate-hf-utils env carries huggingface_hub; the login comes from
HF_HOME's token or the cached login):
    HF_HOME=/mnt/SN850X_8TB_EXT4/Model_Cache/huggingface \\
    ~/miniforge3/envs/cjm-substrate-hf-utils/bin/python scripts/flywheel_backup.py \\
        [--workspace DIR] [--model-repo NAME] [--dataset-repo NAME] [--dry-run] [--no-verify]

EXIT CODES
    0  - every artifact backed up + verified (or already was)
    1  - a verification failed (the sidecar is NOT written for that artifact)
    2  - configuration error (workspace missing, huggingface_hub unavailable, not logged in)
"""

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_WORKSPACE = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills/cjm-transcription-core"
DEFAULT_MODEL_REPO = "cjm-flywheel-training-runs"
DEFAULT_DATASET_REPO = "cjm-flywheel-datasets"
TARGET = "huggingface"
ARTIFACT_CLASSES = (  # (class dir, manifest format, hub repo type)
    ("training-runs", "cjm-capability-pyannote/training-run-manifest", "model"),
    ("datasets", "cjm-transcript-correction-core/dataset-manifest", "dataset"),
)


def _sha256(path: Path) -> str:
    """`sha256:<hex>` over a file's bytes (the manifest's content-hash form)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _lifecycle(artifact_dir: Path):
    """The substrate's ArtifactLifecycle — from the installed cjm-substrate when it carries
    the 0.2.0 sidecar (record_backup), else loaded straight from THIS repo's file: the
    hf-utils env installs an older cjm-substrate from PyPI, so a plain import finds a copy
    without `utils.lifecycle` and leaves the package cached in sys.modules — a sys.path
    fallback would never be consulted."""
    try:
        from cjm_substrate.utils.lifecycle import ArtifactLifecycle
        if hasattr(ArtifactLifecycle, "record_backup"):
            return ArtifactLifecycle(artifact_dir)
    except ImportError:
        pass
    import importlib.util
    src = Path(__file__).resolve().parents[1] / "cjm_substrate" / "utils" / "lifecycle.py"
    spec = importlib.util.spec_from_file_location("_cjm_lifecycle_local", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ArtifactLifecycle(artifact_dir)


def discover_artifacts(workspace: Path) -> List[Dict[str, Any]]:
    """Every flywheel artifact dir whose manifest carries a known format: its id, class,
    hub repo type, the files to push (everything in the dir except the lifecycle
    sidecar, each with its sha256) and ONE content hash over them (sorted relative path
    + sha256 per file) — the idempotence key a re-run compares against the sidecar."""
    out: List[Dict[str, Any]] = []
    for class_dir, fmt, kind in ARTIFACT_CLASSES:
        base = workspace / class_dir
        if not base.is_dir():
            continue
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            try:
                m = json.loads((d / "manifest.json").read_text())
            except (OSError, ValueError):
                continue
            if not (isinstance(m, dict) and m.get("format") == fmt):
                continue
            files = sorted(p for p in d.rglob("*") if p.is_file() and p.name != "lifecycle.json")
            per_file: Dict[str, str] = {}
            digest = hashlib.sha256()
            for p in files:
                rel = p.relative_to(d).as_posix()
                per_file[rel] = _sha256(p)
                digest.update(f"{rel}\0{per_file[rel]}\n".encode("utf-8"))
            out.append({"id": d.name, "class": class_dir, "kind": kind, "dir": d,
                        "files": per_file, "content_hash": "sha256:" + digest.hexdigest(),
                        "size_bytes": sum(p.stat().st_size for p in files)})
    return out


def already_backed_up(artifact: Dict[str, Any], repo_id: str) -> Optional[Dict[str, Any]]:
    """The sidecar's backup record for this repo at the artifact's CURRENT content hash,
    if any — a changed artifact (a re-trained run under the same id, a regenerated
    dataset) is pushed again as a new revision."""
    for b in _lifecycle(artifact["dir"]).backups:
        if (b.get("target") == TARGET and b.get("repo_id") == repo_id
                and b.get("content_hash") == artifact["content_hash"]):
            return b
    return None


def push_artifact(api: Any, artifact: Dict[str, Any], repo_id: str) -> str:
    """Upload the artifact dir under <id>/ in the repo as ONE commit; returns the commit
    sha the push landed as (the revision a fresh pull and the sidecar record name)."""
    info = api.upload_folder(folder_path=str(artifact["dir"]), repo_id=repo_id,
                             repo_type=artifact["kind"], path_in_repo=artifact["id"],
                             ignore_patterns=["lifecycle.json"],
                             commit_message=f"{artifact['id']} @ {artifact['content_hash'][:19]} "
                                            f"(scripts/flywheel_backup.py)")
    return str(getattr(info, "oid", None) or info)


def verify_artifact(artifact: Dict[str, Any], repo_id: str, revision: str) -> List[str]:
    """A FRESH download of <id>/ at the pushed revision into a temp dir (its own cache —
    the shared HF cache never sees the private repo), compared file by file against the
    local sha256s; returns the mismatches (empty = verified)."""
    from huggingface_hub import snapshot_download
    problems: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(snapshot_download(repo_id=repo_id, repo_type=artifact["kind"],
                                       revision=revision,
                                       allow_patterns=[f"{artifact['id']}/*",
                                                       f"{artifact['id']}/**"],
                                       local_dir=str(Path(tmp) / "pull"),
                                       cache_dir=str(Path(tmp) / "cache")))
        for rel, digest in artifact["files"].items():
            p = local / artifact["id"] / rel
            if not p.is_file():
                problems.append(f"missing after pull: {rel}")
            elif _sha256(p) != digest:
                problems.append(f"sha256 mismatch after pull: {rel}")
    return problems


def main(argv: List[str] = None) -> int:
    """CLI: discover, push (skipping what the sidecars already record), verify, record."""
    ap = argparse.ArgumentParser(description="Back up the flywheel's local-only artifacts to private Hugging Face repos.")
    ap.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    ap.add_argument("--model-repo", default=DEFAULT_MODEL_REPO, help="repo NAME under the account for training runs")
    ap.add_argument("--dataset-repo", default=DEFAULT_DATASET_REPO, help="repo NAME under the account for datasets")
    ap.add_argument("--dry-run", action="store_true", help="list what would be pushed; touch nothing")
    ap.add_argument("--no-verify", action="store_true", help="skip the fresh-pull verification (not recommended)")
    args = ap.parse_args(argv)
    ws = Path(args.workspace)
    if not ws.is_dir():
        print(f"workspace missing: {ws}", file=sys.stderr)
        return 2
    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("huggingface_hub is not installed in this interpreter — run under the "
              "cjm-substrate-hf-utils env (see the module docstring)", file=sys.stderr)
        return 2
    api = HfApi()
    try:
        account = api.whoami()["name"]
    except Exception as e:  # noqa: BLE001 — any auth failure is the same verdict
        print(f"not logged in to Hugging Face: {e}", file=sys.stderr)
        return 2
    repos = {"model": f"{account}/{args.model_repo}", "dataset": f"{account}/{args.dataset_repo}"}
    if not args.dry_run:
        for kind, rid in repos.items():
            try:
                api.create_repo(repo_id=rid, repo_type=kind, private=True, exist_ok=True)
            except Exception as e:  # noqa: BLE001 — a 403 here is the token's scope, not a bug
                print(f"cannot create/open {rid}: {e}\n-> the token HF_HOME holds is READ-scoped "
                      f"(the capability workers' token); run this script with a WRITE-scoped token in "
                      f"HF_TOKEN=<token> — never overwrite the workers' token", file=sys.stderr)
                return 2
    artifacts = discover_artifacts(ws)
    pushed = skipped = failed = 0
    for a in artifacts:
        rid = repos[a["kind"]]
        prior = already_backed_up(a, rid)
        mib = a["size_bytes"] / 2 ** 20
        if prior:
            skipped += 1
            print(f"= {a['id']}  already at {rid}/{a['id']}@{str(prior.get('revision'))[:8]}")
            continue
        if args.dry_run:
            print(f"~ {a['id']}  would push {len(a['files'])} file(s), {mib:.1f} MiB -> {rid}/{a['id']}")
            continue
        rev = push_artifact(api, a, rid)
        problems = [] if args.no_verify else verify_artifact(a, rid, rev)
        if problems:
            failed += 1
            print(f"! {a['id']}  pushed as {rev[:8]} but NOT verified: " + "; ".join(problems))
            continue
        _lifecycle(a["dir"]).record_backup(target=TARGET, repo_id=rid, repo_type=a["kind"],
                                           path_in_repo=a["id"], revision=rev,
                                           content_hash=a["content_hash"])
        pushed += 1
        print(f"+ {a['id']}  -> {rid}/{a['id']}@{rev[:8]}  ({mib:.1f} MiB, "
              f"{'unverified' if args.no_verify else 'verified by a fresh pull'})")
    print(f"{len(artifacts)} artifact(s): {pushed} pushed, {skipped} already backed up, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
