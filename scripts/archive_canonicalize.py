"""The archive's one canonicalization commit (design 56c9c332 (6); work item f86be52f).

    python scripts/archive_canonicalize.py [--write] [--detail]

Each kept post -- a post under the corpus root at HEAD, never a retired one, never a site page --
is decomposed exactly as the notes ingest decomposes it and COMPOSED from its elements (the
markdown core's compose_note_text): heading lines in the composed form, a skipped level closed up
and every top level at H2, one blank line after the frontmatter and between elements. The report
lists each post's heading-level changes (old -> new, the title) -- the visible ones, reviewed
before the commit -- and counts the render-neutral ones (heading-line whitespace, blank lines).
--write puts the composed text into the working tree, refusing a file whose working copy differs
from HEAD beyond line endings; the user commits. The proof is scripts/site_main_content_diff.py
over a render before and after: nothing changes but the listed levels.
"""

import argparse
import json
import subprocess
from pathlib import Path

from cjm_context_graph_projection.archive import retired_sources
from cjm_context_graph_projection.devgraph import ArchiveSource
from cjm_context_graph_projection.gitfold import blobs_at
from cjm_markdown_decompose_core.project import (compose_note_text, COMPOSED_TOP_LEVEL,
                                                 section_outline)

NOTES_CONFIG = Path(__file__).resolve().parent.parent / ".cjm" / "notes" / "graph.config.json"


def canonical_posts(
    config: dict,  # The notes graph-sibling config (notes_corpus, website_root, site_pages, journal_path)
) -> list:  # One row per kept post: {path, root, blob, composed, levels, whitespace, blank_lines}
    """Each kept post at HEAD beside its composed form, with the heading changes classified."""
    src = ArchiveSource(config["notes_corpus"], config.get("notes_profile") or "quarto_post",
                        site_root=config["website_root"], site_pages=config.get("site_pages"),
                        retired=retired_sources(config.get("journal_path")))
    held = subprocess.run(["git", "-C", src.top, "ls-tree", "-r", "--name-only", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.split("\n")
    kept = sorted(p for p in held if p and src.is_post(p) and p not in src.by_retired)
    blobs = blobs_at(src.top, "HEAD", kept)
    rows = []
    for rel in kept:
        blob = blobs[rel].decode("utf-8")
        note, _ = src.decompose(rel, blobs[rel])
        parent, following = section_outline(note.sections)
        composed = compose_note_text(note.frontmatter_raw, note.sections, parent, following)
        by_id = {s.id: s for s in note.sections}

        def depth(sid: str) -> int:
            return 0 if sid not in parent else depth(parent[sid]) + 1

        levels, whitespace = [], 0
        for s in note.sections:
            if s.level <= 0:
                continue
            new = COMPOSED_TOP_LEVEL + depth(s.id)
            if new != s.level:
                levels.append((s.level, new, s.title))
            elif by_id[s.id].raw.split("\n", 1)[0] != "#" * new + " " + s.title:
                whitespace += 1
        rows.append({"path": rel, "root": src.top, "blob": blob, "composed": composed, "levels": levels,
                     "whitespace": whitespace,
                     "blank_lines": blob.count("\n") - composed.count("\n")})
    return rows


def main(
    argv: list = None,  # The command line (default: sys.argv)
) -> int:  # The exit status
    ap = argparse.ArgumentParser(description="The archive's canonicalization commit (design 56c9c332 (6))")
    ap.add_argument("--write", action="store_true", help="Write each composed post into the working tree")
    ap.add_argument("--detail", action="store_true", help="List every heading-level change, not three per post")
    ap.add_argument("--config", default=str(NOTES_CONFIG), help="The notes graph-sibling config")
    args = ap.parse_args(argv)
    rows = canonical_posts(json.loads(Path(args.config).read_text(encoding="utf-8")))
    changed = [r for r in rows if r["composed"] != r["blob"]]
    leveled = [r for r in rows if r["levels"]]
    print(f"{len(changed)} of {len(rows)} kept posts change: {sum(len(r['levels']) for r in rows)} heading "
          f"level(s) in {len(leveled)} post(s) -- visible; {sum(r['whitespace'] for r in rows)} heading "
          f"line(s) by whitespace and {sum(r['blank_lines'] for r in rows)} blank line(s) -- render-neutral")
    for r in leveled:
        print(f"- {r['path']}: {len(r['levels'])} level change(s)")
        for old, new, title in r["levels"] if args.detail else r["levels"][:3]:
            print(f"    H{old} -> H{new}  {title}")
        if not args.detail and len(r["levels"]) > 3:
            print(f"    ... {len(r['levels']) - 3} more")
    if not args.write:
        return 0
    refused = []
    for r in changed:
        path = Path(r["root"]) / r["path"]
        work = path.read_bytes()
        if work.replace(b"\r\n", b"\n") != r["blob"].encode("utf-8"):
            refused.append(r["path"])  # an uncommitted edit: never overwritten
            continue
        path.write_text(r["composed"], encoding="utf-8")
    print(f"wrote {len(changed) - len(refused)} post(s)"
          + (f"; refused {len(refused)} with uncommitted edits: {', '.join(refused)}" if refused else ""))
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
