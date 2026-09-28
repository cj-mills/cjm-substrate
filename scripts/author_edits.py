"""Run journaled `cg-write author --edit` ops from a spec file (argv lists, no shell quoting).

    python scripts/author_edits.py EDITS.spec

A module or note id routes each edit to the one slot holding OLD; the run stops at the first
refused edit, and the edits before it stand (each is journaled). Craft register section of
2026-09-28 (the Series build 38f1fd96).

Spec format (repeatable blocks):
@@@ <node id>
<old text>
===
<new text>
@@@END
Stops at the first failing edit.
"""

import subprocess
import sys

W = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills/cjm-substrate/.cjm/bin/cg-write"


def parse(text):
    blocks = []
    parts = text.split("@@@END\n")
    for p in parts:
        p = p.lstrip("\n")
        if not p.strip():
            continue
        assert p.startswith("@@@ "), p[:80]
        head, body = p.split("\n", 1)
        node = head[4:].strip()
        old, new = body.split("\n===\n", 1)
        blocks.append((node, old, new.rstrip("\n") if not new.endswith("\n\n") else new[:-1]))
    return blocks


def main():
    blocks = parse(open(sys.argv[1]).read())
    for i, (node, old, new) in enumerate(blocks, 1):
        r = subprocess.run([W, "author", node, "--edit", old, new], capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip().splitlines()
        tail = [l for l in out if l.strip()][-3:]
        print(f"[{i}/{len(blocks)}] rc={r.returncode} " + " | ".join(tail)[:400])
        if r.returncode != 0 or any("error" in l.lower() or "refus" in l.lower() for l in tail):
            print("STOPPED")
            sys.exit(1)


if __name__ == "__main__":
    main()
