"""JSON sidecar for shell view-state — settings and bookmarks that persist
across sessions without ever being knowledge (born in the tui-kit as its
state module, work item aafce2c6; absorbed here by the spine-absorption
structure 12f342f1 so the workflow cores' state modules depend on the
substrate every shell already shares, not on a toolkit kit).

View state, not knowledge: what the operator picked or where the eye was
belongs in a local sidecar next to the thing it describes, never in a graph
write. The store is deliberately forgiving — load returns {} on an absent or
corrupt file (never raises), and writes are best-effort (a read-only location
must never break the loop the shell is driving). Path conventions stay with
the consumer: the transcription shells key off their manifests dir's parent,
the correction shells suffix their graph db path.
"""

import json
from pathlib import Path
from typing import Any, Dict


class SidecarState:
    """One JSON sidecar file: never-raise load, merge-on-save, best-effort write.

    The consumer owns the path convention and any nested-shape policy; this
    class owns only the forgiveness contract (absent/corrupt reads as {},
    write failures silently tolerated). For flat key merges use `save`; for
    nested shapes (per-source bookmark dicts), `load` -> mutate -> `write`.
    """

    def __init__(
        self,
        path,  # Where the sidecar lives (str or Path)
    ):
        self.path = Path(path)

    def load(self) -> Dict[str, Any]:  # Persisted state ({} when absent/unreadable — never raises)
        """Read the persisted state."""
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def write(self, state: Dict[str, Any]) -> None:
        """Write the full state (best-effort: a read-only location must not
        break the flow the shell is driving)."""
        try:
            self.path.write_text(json.dumps(state, indent=2) + "\n")
        except OSError:
            pass

    def save(self, **updates: Any) -> Dict[str, Any]:  # The merged state as written
        """Merge updates into the persisted state and write it back."""
        state = self.load()
        state.update(updates)
        self.write(state)
        return state
