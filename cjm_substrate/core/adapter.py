"""The typed-task half of the capability-unit fracture (pass-2 Thread 3) —
base mechanic for the per-task adapter interface libraries."""

from abc import ABC
from typing import ClassVar, Optional


class TaskAdapter(ABC):
    """Base for task adapters — the typed-task half of the capability-unit
    fracture (pass-2 Thread 3).

    Subclasses (one ABC per task, in `cjm-<task>-adapter-interface` libraries)
    declare:

    - the TYPED task method (the contract `execute(*args, **kwargs)` never
      gave the task), abstract on the per-task ABC;
    - `task_name`: the task this adapter serves (e.g. "transcription");
    - `required_tool_protocol`: the structural contract required of a tool
      capability (a `typing.Protocol`; provisional `None` until the
      protocol is evidence-locked — Q5 posture: declare the slot, let
      stage-4/8 tool-splitting evidence finalize the protocol bodies);
    - the task's persistence helpers (storage classes), beside the task
      method rather than on it.

    Implementations run in-worker beside their tool capability. The base is
    deliberately mechanism-light: registry/routing is CR-17 pt 2 (stage 4).
    """

    task_name: ClassVar[str] = ""                             # e.g. "transcription"
    required_tool_protocol: ClassVar[Optional[type]] = None   # typing.Protocol; None = provisional
