"""Artifact lifecycle sidecar — the ONE seam every picker filters through
(work item b20cb911, DEC 14678fc9): proposal sets, training runs, extracted
datasets and their successors accumulate under the workspace as
<class-dir>/<artifact-id>/manifest.json, and nothing ever retired one short
of a shell `rm`. Retirement is a VISIBILITY question, not deletion (the
65cdd573 spine reasoning; correction sessions carry a purpose= that
extraction filters on — same shape), so the state lives in a sidecar beside
the manifest: <artifact-dir>/lifecycle.json. The manifest itself is
CAPABILITY OUTPUT and stays immutable; the sidecar travels with the
directory; an absent sidecar reads as active.

Deliberately a LEAF fact: the sidecar records state + history (at / actor /
reason) and nothing else — no identity, no cross-references. When the
flywheel's artifacts earn graph identity (discussion item 03cc8e2e) the
sidecars ingest as assertions and the indexes swap this seam's store;
nothing here prejudices that design. Who holds a reference to an artifact
is COMPUTED off the workspace's manifests (find_holders — a literal id scan,
capability-generic: ids are content hashes, so a substring hit is a
reference), never recorded.

Two verbs the shells expose: archive / unarchive (reversible, always
allowed — an archived artifact stays inspectable and its holders keep
resolving; only the pickers hide it). DELETE is the one destructive verb:
refused unless the artifact is ARCHIVED and has ZERO holders, and the
refusal names every holder. The module doubles as the CLI for every
artifact class (`python -m cjm_substrate.utils.lifecycle --help`).
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

FORMAT = "cjm-substrate/artifact-lifecycle"
VERSION = "0.1.0"
SIDECAR_NAME = "lifecycle.json"
MANIFEST_NAME = "manifest.json"
ACTIVE = "active"
ARCHIVED = "archived"
STATES = (ACTIVE, ARCHIVED)
# Where a workspace's manifests name other artifacts by id: the decomp run
# manifests (event_propset_id per source + legacy path pointers), every
# artifact class's own manifest (training_run_id / dataset_id), and the
# workspace marker's flywheel pin.
DEFAULT_HOLDER_GLOBS = ("runs/*.json", "proposals/*/manifest.json",
                        "training-runs/*/manifest.json",
                        "datasets/*/manifest.json", "*.yaml")


class LifecycleRefusal(ValueError):
    """A lifecycle verb refused LOUDLY (not an artifact dir, an unknown
    state, a delete on an active or still-referenced artifact)."""


def default_actor() -> str:  # Who a lifecycle event is stamped with
    """CJM_ACTOR when the environment carries one (the wrappers stamp
    agent:session / user:workbench), else user:cli."""
    return os.environ.get("CJM_ACTOR") or "user:cli"


def artifact_id(artifact_dir) -> str:  # The artifact's id = its directory name
    """Every artifact class names its directory by the id its manifest
    carries (proposal_set_id / run_id / dataset_id), so the dir name is the
    capability-generic id."""
    return Path(artifact_dir).name


class ArtifactLifecycle:
    """One artifact directory's lifecycle sidecar: forgiving reads (absent /
    corrupt / foreign = active with no history), deliberate writes (a
    lifecycle change is a verb, so a write failure RAISES — unlike view
    state, silently losing it would lie to the next picker)."""

    def __init__(self, artifact_dir):  # The <class-dir>/<artifact-id>/ directory (str or Path)
        self.dir = Path(artifact_dir)
        self.path = self.dir / SIDECAR_NAME

    @property
    def manifest_path(self) -> Path:  # The manifest this sidecar sits beside
        return self.dir / MANIFEST_NAME

    def exists(self) -> bool:  # Whether the dir is an artifact at all (has a manifest)
        return self.manifest_path.is_file()

    def load(self) -> Dict[str, Any]:  # {format, version, state, history} — never raises
        rec: Dict[str, Any] = {"format": FORMAT, "version": VERSION,
                               "state": ACTIVE, "history": []}
        try:
            got = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return rec
        if not (isinstance(got, dict) and got.get("format") == FORMAT):
            return rec
        state = str(got.get("state") or ACTIVE)
        rec["state"] = state if state in STATES else ACTIVE
        hist = got.get("history")
        rec["history"] = [h for h in hist if isinstance(h, dict)] \
            if isinstance(hist, list) else []
        return rec

    @property
    def state(self) -> str:  # active | archived
        return str(self.load()["state"])

    def set_state(
        self,
        state: str,                    # active | archived
        *,
        actor: Optional[str] = None,   # Who (default: CJM_ACTOR / user:cli)
        reason: Optional[str] = None,  # Free text the history keeps
        at: Optional[float] = None,    # Event time (default: now)
    ) -> Tuple[Dict[str, Any], bool]:  # (the record as written, whether it changed)
        """Land a state; same-state is a no-op (no history noise, no write).
        Refuses a non-artifact dir and an unknown state — both loud."""
        if state not in STATES:
            raise LifecycleRefusal(f"unknown lifecycle state {state!r} "
                                   f"(one of {', '.join(STATES)})")
        if not self.exists():
            raise LifecycleRefusal(f"{self.dir} is not an artifact directory "
                                   f"(no {MANIFEST_NAME})")
        rec = self.load()
        if rec["state"] == state:
            return rec, False
        rec["state"] = state
        rec["history"].append({"state": state,
                               "at": float(at if at is not None else time.time()),
                               "actor": actor or default_actor(),
                               "reason": reason or ""})
        self.path.write_text(json.dumps(rec, indent=2) + "\n")
        return rec, True

    def archive(self, **kw: Any) -> Tuple[Dict[str, Any], bool]:  # set_state(ARCHIVED)
        return self.set_state(ARCHIVED, **kw)

    def unarchive(self, **kw: Any) -> Tuple[Dict[str, Any], bool]:  # set_state(ACTIVE)
        return self.set_state(ACTIVE, **kw)

    def delete(
        self,
        *,
        holders: Sequence[str] = (),  # Files still naming this artifact (find_holders)
        dry_run: bool = False,        # Report what would go, remove nothing
    ) -> Path:  # The directory removed (or that would be)
        """The one destructive verb: only an ARCHIVED artifact with ZERO
        holders may go, and a refusal names every holder."""
        if not self.exists():
            raise LifecycleRefusal(f"{self.dir} is not an artifact directory "
                                   f"(no {MANIFEST_NAME})")
        if self.state != ARCHIVED:
            raise LifecycleRefusal(f"{artifact_id(self.dir)} is {self.state} — "
                                   f"archive it first; delete serves archived "
                                   f"artifacts only")
        if holders:
            raise LifecycleRefusal(
                f"{artifact_id(self.dir)} is still referenced by "
                f"{len(holders)} file(s): " + ", ".join(str(h) for h in holders))
        if not dry_run:
            shutil.rmtree(self.dir)
        return self.dir


def lifecycle_state(manifest_path) -> str:  # State of the artifact owning a manifest
    """The index-row question: given a row's manifest path, its state."""
    return ArtifactLifecycle(Path(manifest_path).parent).state


def stamp_lifecycle(
    rows: Iterable[Dict[str, Any]],  # Index rows (manifest dicts carrying their path)
    key: str = "_path",              # The row key holding the manifest path
) -> None:
    """Stamp each row's `_lifecycle` in place (rows without a path read active)."""
    for m in rows:
        p = m.get(key)
        m["_lifecycle"] = lifecycle_state(p) if p else ACTIVE


def partition_lifecycle(
    rows: Iterable[Dict[str, Any]],  # Index rows, any order (order preserved per side)
    key: str = "_path",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (active, archived), each stamped
    """Stamp + split: the active side is what a picker lists by default,
    the archived side what its show-archived toggle adds back."""
    rows = list(rows)
    stamp_lifecycle(rows, key)
    active = [m for m in rows if m.get("_lifecycle") != ARCHIVED]
    archived = [m for m in rows if m.get("_lifecycle") == ARCHIVED]
    return active, archived


def find_holders(
    root,                                    # Workspace root to scan
    needle: str,                             # The artifact id (dir name)
    *,
    globs: Sequence[str] = DEFAULT_HOLDER_GLOBS,
    exclude_dir=None,                        # The artifact's own dir (its manifest names itself)
) -> List[str]:  # Root-relative paths of files naming the artifact
    """Every manifest/marker under the workspace that names the artifact —
    a literal scan (ids are hashes; a hit IS a reference), capability-
    generic by construction: no field vocabulary, so a new artifact class
    or a legacy path pointer is covered the day it appears."""
    root = Path(root)
    skip = Path(exclude_dir).resolve() if exclude_dir else None
    out: List[str] = []
    seen: set = set()
    for g in globs:
        try:
            files = sorted(root.glob(g))
        except OSError:
            continue
        for f in files:
            if not f.is_file() or f in seen:
                continue
            seen.add(f)
            if skip is not None:
                try:
                    f.resolve().relative_to(skip)
                    continue
                except ValueError:
                    pass
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            if needle in text:
                out.append(str(f.relative_to(root)))
    return out


def workspace_root_for(artifact_dir) -> Path:  # CJM_WORKSPACE, else <class-dir>'s parent
    ws = os.environ.get("CJM_WORKSPACE")
    return Path(ws) if ws else Path(artifact_dir).resolve().parent.parent


def list_artifacts(
    class_dir,                       # proposals/ | training-runs/ | datasets/ …
    *,
    include_archived: bool = True,
) -> List[Dict[str, Any]]:  # [{id, path, state, history}] in name order
    """Every artifact directory under a class dir (a dir with a manifest)
    with its lifecycle — the CLI's list and a picker's audit view."""
    out: List[Dict[str, Any]] = []
    try:
        children = sorted(p for p in Path(class_dir).iterdir() if p.is_dir())
    except OSError:
        return out
    for d in children:
        lc = ArtifactLifecycle(d)
        if not lc.exists():
            continue
        rec = lc.load()
        if rec["state"] == ARCHIVED and not include_archived:
            continue
        out.append({"id": artifact_id(d), "path": str(d),
                    "state": rec["state"], "history": rec["history"]})
    return out


def _fmt_history(hist: List[Dict[str, Any]]) -> str:
    if not hist:
        return ""
    h = hist[-1]
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(h.get("at") or 0)))
    why = f" — {h['reason']}" if h.get("reason") else ""
    return f" ({h.get('state')} {when} by {h.get('actor')}{why})"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m cjm_substrate.utils.lifecycle",
        description="Artifact lifecycle: list / archive / unarchive / holders / "
                    "delete over <class-dir>/<artifact-id>/manifest.json trees.")
    sub = p.add_subparsers(dest="verb", required=True)
    ls = sub.add_parser("list", help="every artifact under a class dir + its state")
    ls.add_argument("class_dir")
    ls.add_argument("--archived-only", action="store_true")
    ls.add_argument("--active-only", action="store_true")
    for verb in ("archive", "unarchive"):
        s = sub.add_parser(verb, help=f"{verb} one artifact (reversible)")
        s.add_argument("artifact_dir")
        s.add_argument("--reason", default=None)
        s.add_argument("--actor", default=None)
    h = sub.add_parser("holders", help="files under the workspace naming the artifact")
    h.add_argument("artifact_dir")
    h.add_argument("--workspace", default=None)
    d = sub.add_parser("delete", help="rm an ARCHIVED artifact nothing references")
    d.add_argument("artifact_dir")
    d.add_argument("--workspace", default=None)
    d.add_argument("--dry-run", action="store_true")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    out = sys.stdout
    try:
        if args.verb == "list":
            rows = list_artifacts(args.class_dir)
            if args.archived_only:
                rows = [r for r in rows if r["state"] == ARCHIVED]
            if args.active_only:
                rows = [r for r in rows if r["state"] == ACTIVE]
            for r in rows:
                out.write(f"{r['state']:<8} {r['id']}{_fmt_history(r['history'])}\n")
            out.write(f"{len(rows)} artifact(s) under {args.class_dir}\n")
            return 0
        lc = ArtifactLifecycle(args.artifact_dir)
        if args.verb in ("archive", "unarchive"):
            rec, changed = getattr(lc, args.verb)(actor=args.actor, reason=args.reason)
            out.write(f"{artifact_id(lc.dir)}: {rec['state']}"
                      f"{'' if changed else ' (already)'}\n")
            return 0
        root = Path(args.workspace) if args.workspace else workspace_root_for(lc.dir)
        holders = find_holders(root, artifact_id(lc.dir), exclude_dir=lc.dir)
        if args.verb == "holders":
            for h in holders:
                out.write(f"{h}\n")
            out.write(f"{len(holders)} holder(s) of {artifact_id(lc.dir)} under {root}\n")
            return 0
        gone = lc.delete(holders=holders, dry_run=args.dry_run)
        out.write(f"{'would delete' if args.dry_run else 'deleted'} {gone}\n")
        return 0
    except LifecycleRefusal as e:
        sys.stderr.write(f"refused: {e}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
