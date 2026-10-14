"""The git allow rules of .claude/settings.local.json, generated from the window's ONE inventory.

One EXACT-PATH rule per repo per subcommand -- `Bash(git -C <checkout> add *)`, `Bash(git -C
<checkout> commit *)`, `Bash(git -C <checkout> push)` -- for every repo `pypi_sweep.sweep()`
names (every non-archived cjm-* repo with a pyproject), so `git -C` keeps working across the
sibling checkouts without a prompt. A `git -C` rule with a `*` before the subcommand
(`git -C * add *`, a `cjm-*` path) also matches options inserted at that position (`-c`,
`--exec-path` run arbitrary commands), so Claude Code warns on it at startup: every such rule
is removed. The generated rules are rewritten whole on each run -- a repo added to or archived
from the inventory gains or loses its rules -- and every other rule keeps its place.

    python scripts/claude_git_rules.py           # the plan: rules added, removed, kept
    python scripts/claude_git_rules.py --write   # rewrite the settings file
"""

import json
import re
import sys
from pathlib import Path
from typing import List

from pypi_sweep import ROOT, sweep

SETTINGS = Path(__file__).resolve().parent.parent / ".claude" / "settings.local.json"
SUBCOMMANDS = ("add *", "commit *", "push")  # One rule each per checkout
GIT_C = re.compile(r"^Bash\(git -C (?P<path>\S+) (?P<rest>.*)\)$")


def generated_rules(repos: List[str]) -> List[str]:  # The exact-path rules for these repo dirs
    """One rule per repo per subcommand, the checkout's path spelled out literally."""
    return [f"Bash(git -C {ROOT / r} {sub})" for r in sorted(repos) for sub in SUBCOMMANDS]


def is_replaced(rule: str) -> bool:  # A `git -C` rule this generator owns or forbids
    """Every `git -C` rule: a wildcarded one (the startup warning) or an exact one this
    generator wrote last time -- both are re-derived from the inventory."""
    return bool(GIT_C.match(rule))


def plan(allow: List[str], repos: List[str]) -> dict:  # {allow, added, removed}
    """The new allow list: the old one less every `git -C` rule, the generated rules where the
    first removed rule stood (else at the end)."""
    new = generated_rules(repos)
    kept, at = [], None
    for rule in allow:
        if is_replaced(rule):
            at = len(kept) if at is None else at
        else:
            kept.append(rule)
    at = len(kept) if at is None else at
    out = kept[:at] + new + kept[at:]
    return {"allow": out, "added": sorted(set(new) - set(allow)),
            "removed": sorted(r for r in allow if is_replaced(r) and r not in new)}


def main(argv: List[str]) -> int:
    settings = json.loads(SETTINGS.read_text())
    allow = settings.setdefault("permissions", {}).setdefault("allow", [])
    repos = [row["repo"] for row in sweep()]
    p = plan(allow, repos)
    print(f"{len(repos)} repos in the inventory · {len(p['added'])} rule(s) added · "
          f"{len(p['removed'])} removed")
    for r in p["removed"]:
        print(f"  - {r}")
    for r in p["added"]:
        print(f"  + {r}")
    if "--write" in argv:
        settings["permissions"]["allow"] = p["allow"]
        SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
        print(f"wrote {SETTINGS}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
