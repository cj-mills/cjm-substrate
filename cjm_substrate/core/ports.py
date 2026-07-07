"""Capability compositions as DAGs of invocation nodes with typed input/output
bindings — the execution-time-bound primitive that replaces submit_sequence
(pass-2 Thread 4 / CR-16; execution stage 3 of the post-pass-2 sequence).
Bindings are OutputRef markers inside node kwargs; the DAG topology is DERIVED
from the markers, member jobs are created lazily when a node's dependencies
complete, and field extraction is centralized in the single substrate-owned
successor of the retired per-consumer field_of helpers. Design rationale:
on-graph note ports-design-provenance."""

import logging
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from cjm_substrate.core.errors import JobError

logger = logging.getLogger(__name__)


class NodeState(str, Enum):
    """State of one composition node (and, for the terminal subset, of a
    whole composition run).

    `skipped` is composition-specific: a node whose transitive dependencies
    failed/cancelled can never run (its inputs will never exist) and is
    recorded as skipped rather than getting a Job at all. Composition-level
    status uses the running/completed/failed/cancelled subset.
    """
    pending = "pending"      # Waiting on dependencies (no Job exists yet)
    running = "running"      # Member Job created and enqueued/executing
    completed = "completed"  # Member Job completed successfully
    failed = "failed"        # Member Job failed
    cancelled = "cancelled"  # Member Job cancelled (or pending member cancelled)
    skipped = "skipped"      # Dependencies failed/cancelled; node never ran


# Node states from which no further transition happens.
TERMINAL_NODE_STATES = {
    NodeState.completed, NodeState.failed, NodeState.cancelled, NodeState.skipped,
}


@dataclass(frozen=True)
class OutputRef:
    """Binding marker: this kwarg's value comes from an upstream node's result.

    Placed directly in a `CompositionNode.kwargs` value position. `field=None`
    binds the WHOLE result (fan-in folds); a field name extracts one field via
    `extract_output_field` (dict key or typed-result attribute). Frozen so
    markers are hashable + safely shareable across nodes.
    """
    node: str  # Producer node id (within the same composition)
    field: Optional[str] = None  # None = whole result; else one field extracted


@dataclass
class CompositionNode:
    """One capability invocation in a composition.

    `kwargs` mixes static values with `OutputRef` markers; the markers are
    scanned (top-level values only — nested containers are not searched, by
    design: evidence needs single-position bindings, and a nested-marker
    grammar is seam-admitted later) to derive the node's dependencies.
    """
    id: str  # Unique node id within the composition
    capability_instance_id: str  # Target capability instance
    kwargs: Dict[str, Any] = field(default_factory=dict)  # Static values + OutputRef markers
    priority: int = 0  # Per-node priority override (0 = inherit composition priority)
    task_name: Optional[str] = None  # Task-channel address: adapter task (stage 4; None = execute channel)
    method: Optional[str] = None  # Task-channel address: adapter method (set with task_name)
    control: Dict[str, Any] = field(default_factory=dict)  # Per-call control flags (force/cache-bypass); threaded into the member Job's CallEnvelope.control


@dataclass
class Composition:
    """A static DAG of capability-invocation nodes, submitted as one unit.

    `fail_fast=True` (default, matching the audit-locked sequence default):
    on a member failure, pending independent members are cancelled, in-flight
    members run to completion, transitive dependents are skipped, and the
    composition lands `failed`. `fail_fast=False` is best-effort: independent
    members continue; only transitive dependents of the failure are skipped.

    `run_id` / `actor` (CR-14 follow-up) are host-tier correlation tags
    stamped onto every lazily-created member Job (the `submit(run_id=,
    actor=)` analog for compositions) — NOT the composition run's own id,
    which the queue assigns at submit.
    """
    nodes: List[CompositionNode]  # The invocation nodes (order = readability + ready-scan order)
    fail_fast: bool = True  # Halt independent pending members on first failure
    priority: int = 0  # Composition-level priority (per-node override possible)
    run_id: Optional[str] = None  # Host-tier run correlation for member Jobs (CR-14 follow-up)
    actor: Optional[str] = None  # Who/what initiated the work (CR-14 follow-up)


class CompositionValidationError(ValueError):
    """A composition failed submit-time validation (duplicate ids, unresolved
    `OutputRef` targets, or a dependency cycle)."""
    pass


def _node_dependencies(
    node: CompositionNode,  # Node whose kwargs are scanned for OutputRef markers
) -> Set[str]:  # Producer node ids this node depends on
    """Derive a node's dependencies from the `OutputRef` markers in its kwargs.

    Top-level kwarg values only (see `CompositionNode` docstring); duplicate
    references to the same producer collapse into one dependency edge.
    """
    return {v.node for v in node.kwargs.values() if isinstance(v, OutputRef)}


def validate_composition(
    comp: Composition,  # Composition to validate
) -> Dict[str, Set[str]]:  # node_id -> set of upstream node ids (the derived DAG)
    """Validate a composition and return its derived dependency map.

    Raises `CompositionValidationError` on duplicate node ids, `OutputRef`
    targets that name no node in the composition, or dependency cycles.
    An empty composition is valid (returns `{}`) — the queue completes it
    at submit, mirroring the empty-sequence totality precedent.
    """
    counts = Counter(n.id for n in comp.nodes)
    dups = sorted(i for i, c in counts.items() if c > 1)
    if dups:
        raise CompositionValidationError(f"duplicate node ids: {dups}")

    known = set(counts)
    deps: Dict[str, Set[str]] = {}
    for n in comp.nodes:
        refs = _node_dependencies(n)
        unknown = sorted(refs - known)
        if unknown:
            raise CompositionValidationError(
                f"node {n.id!r} references unknown node(s): {unknown}")
        deps[n.id] = refs

    # Kahn's algorithm: peel zero-dependency nodes; leftovers imply a cycle.
    remaining = {nid: set(ds) for nid, ds in deps.items()}
    queue = deque(nid for nid, ds in remaining.items() if not ds)
    dependents: Dict[str, Set[str]] = defaultdict(set)
    for nid, ds in deps.items():
        for d in ds:
            dependents[d].add(nid)
    resolved = 0
    while queue:
        nid = queue.popleft()
        resolved += 1
        for child in dependents.get(nid, ()):
            remaining[child].discard(nid)
            if not remaining[child]:
                queue.append(child)
    if resolved != len(deps):
        cyclic = sorted(nid for nid, ds in remaining.items() if ds)
        raise CompositionValidationError(f"dependency cycle involving: {cyclic}")

    return deps


class CompositionBindingError(RuntimeError):
    """An `OutputRef` could not be resolved against the producer's recorded
    result at execution time (missing producer result, missing key/attribute)."""
    pass


def extract_output_field(
    result: Any,  # The producer node's recorded result (typed DTO or capability-side dict)
    field: Optional[str],  # Field to extract; None returns the whole result
    producer: str = "?",  # Producer node id (error messages only)
) -> Any:  # The bound value
    """Extract a field from an upstream result for binding into a kwarg.

    The single substrate-owned successor of the retired `field_of` helpers:
    dicts resolve by KEY (intent for capability-side dict results), everything
    else by ATTRIBUTE (typed wire DTOs). Missing fields raise
    `CompositionBindingError` loudly — silent shape-shifting is what stage 2
    retired (F12 fail-loudly posture).
    """
    if field is None:
        return result
    if isinstance(result, dict):
        if field in result:
            return result[field]
        raise CompositionBindingError(
            f"result of node {producer!r} has no key {field!r} "
            f"(available: {sorted(result)[:20]})")
    if hasattr(result, field):
        return getattr(result, field)
    raise CompositionBindingError(
        f"result of node {producer!r} ({type(result).__name__}) "
        f"has no field {field!r}")


def resolve_node_kwargs(
    node: CompositionNode,  # Node whose kwargs are being materialized
    results: Dict[str, Any],  # Completed producers' results, keyed by node id
) -> Dict[str, Any]:  # kwargs with every OutputRef replaced by its bound value
    """Materialize a node's kwargs by resolving its `OutputRef` markers.

    Called by the executor at the moment a node becomes ready (all
    dependencies completed) — this is where execution-time binding actually
    happens. Static kwargs pass through untouched.
    """
    resolved: Dict[str, Any] = {}
    for k, v in node.kwargs.items():
        if isinstance(v, OutputRef):
            if v.node not in results:
                raise CompositionBindingError(
                    f"node {node.id!r} kwarg {k!r} references {v.node!r}, "
                    f"whose result is not recorded (executor ordering bug)")
            resolved[k] = extract_output_field(results[v.node], v.field, producer=v.node)
        else:
            resolved[k] = v
    return resolved


@dataclass
class CompositionNodeRun:
    """Live state of one node within a composition run."""
    node_id: str  # The CompositionNode this tracks
    state: NodeState = NodeState.pending  # Current node state
    job_id: Optional[str] = None  # Member Job id (set when the node starts)
    result: Any = None  # Member job result (if completed)
    error: Optional[JobError] = None  # Structured failure summary (if failed/cancelled)


@dataclass
class CompositionRun:
    """Tracks a submitted composition through execution (lives in
    `JobQueue._compositions`).

    Carries the validated dependency map (and its reverse) so advancement
    decisions are O(edges) lookups for the rest of the run. Composition-level
    `status` reuses the NodeState terminal subset: starts `running`,
    transitions to completed / failed / cancelled via
    `derive_terminal_status` once `all_terminal()`.

    `cancel_requested` records USER cancel intent (`cancel_composition`),
    distinguishing it from the executor's fail-fast HOUSEKEEPING cancels of
    independent pending members after a failure — without the flag, a
    failure-driven run would derive `cancelled` instead of `failed` because
    its housekeeping cancels would dominate.

    The advancement logic (ready-set, skips, terminal derivation) lives HERE
    as pure methods so it is unit-testable without a queue — the executor in
    `core.queue` stays a thin integration: create lazy Jobs for ready nodes,
    record member outcomes, re-scan.
    """
    id: str  # Composition run UUID
    composition: Composition  # The submitted spec (immutable post-submit)
    deps: Dict[str, Set[str]]  # node_id -> upstream node ids (validated)
    dependents: Dict[str, Set[str]]  # node_id -> downstream node ids (reverse of deps)
    nodes_by_id: Dict[str, CompositionNode]  # Spec lookup for the executor
    node_runs: Dict[str, CompositionNodeRun]  # Per-node live state
    status: NodeState = NodeState.running  # Composition-level status
    cancel_requested: bool = False  # True once cancel_composition is called (user intent)
    submitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None  # Set when the run reaches terminal status

    def ready_nodes(
        self,
    ) -> List[str]:  # Node ids that are pending with all dependencies completed
        """Nodes whose member Jobs can be created right now.

        Scan order follows the composition's node order (readability +
        deterministic dispatch among equally-ready nodes).
        """
        out: List[str] = []
        for n in self.composition.nodes:
            nr = self.node_runs[n.id]
            if nr.state != NodeState.pending:
                continue
            if all(self.node_runs[d].state == NodeState.completed for d in self.deps[n.id]):
                out.append(n.id)
        return out

    def record_started(
        self,
        node_id: str,  # Node whose member Job was just created/enqueued
        job_id: str,  # The member Job's id
    ) -> None:
        """Mark a node running and bind it to its member Job."""
        nr = self.node_runs[node_id]
        nr.state = NodeState.running
        nr.job_id = job_id

    def record_result(
        self,
        node_id: str,  # Node whose member Job reached terminal status
        state: NodeState,  # completed / failed / cancelled
        result: Any = None,  # Member job result (if completed)
        error: Optional[JobError] = None,  # Structured failure (if failed/cancelled)
    ) -> None:
        """Record a member job's terminal outcome on its node."""
        if state not in TERMINAL_NODE_STATES:
            raise ValueError(f"record_result requires a terminal state, got {state}")
        nr = self.node_runs[node_id]
        nr.state = state
        nr.result = result
        nr.error = error

    def skip_dependents(
        self,
        node_id: str,  # Node whose failure/cancellation poisons its downstream
    ) -> List[str]:  # Node ids newly marked skipped (transitive)
        """Mark every still-pending transitive dependent of `node_id` skipped.

        Skipped nodes never get a Job — their inputs can never exist. Runs
        regardless of fail_fast (dependents are unrunnable either way; fail_fast
        only governs INDEPENDENT pending members, which the executor cancels).
        """
        skipped: List[str] = []
        frontier = deque(self.dependents.get(node_id, ()))
        seen: Set[str] = set()
        while frontier:
            nid = frontier.popleft()
            if nid in seen:
                continue
            seen.add(nid)
            nr = self.node_runs[nid]
            if nr.state == NodeState.pending:
                nr.state = NodeState.skipped
                skipped.append(nid)
            frontier.extend(self.dependents.get(nid, ()))
        return skipped

    def all_terminal(
        self,
    ) -> bool:  # True when every node is in a terminal state
        """Whether the composition has nothing left to run or wait for."""
        return all(nr.state in TERMINAL_NODE_STATES for nr in self.node_runs.values())

    def derive_terminal_status(
        self,
    ) -> NodeState:  # cancelled / failed / completed
        """Derive the composition-level terminal status from member outcomes.

        Precedence:
        1. USER cancel intent (`cancel_requested`) dominates everything.
        2. A member failure under fail_fast lands the run `failed` — the
           executor's housekeeping cancels of independent pending members do NOT
           flip it to cancelled (that's what `cancel_requested` distinguishes).
        3. A directly-cancelled member (job-level cancel, no failure, no
           composition intent) lands the run `cancelled`.
        4. Otherwise `completed` — including best-effort (fail_fast=False) runs
           with failed members: "we attempted everything", matching the sequence
           semantics this replaces. Per-node outcomes stay inspectable on
           `node_runs` either way. (`skipped` never appears without a failed or
           cancelled member upstream of it, so it needs no clause of its own.)
        """
        states = {nr.state for nr in self.node_runs.values()}
        if self.cancel_requested:
            return NodeState.cancelled
        if NodeState.failed in states:
            if self.composition.fail_fast:
                return NodeState.failed
            return (NodeState.cancelled if NodeState.cancelled in states
                    else NodeState.completed)
        if NodeState.cancelled in states:
            return NodeState.cancelled
        return NodeState.completed

    def results_by_node(
        self,
    ) -> Dict[str, Any]:  # node_id -> result, for completed nodes only
        """Completed members' results keyed by node id (what host folds consume,
        and what `resolve_node_kwargs` reads at advancement time)."""
        return {nid: nr.result for nid, nr in self.node_runs.items()
                if nr.state == NodeState.completed}


def new_composition_run(
    comp: Composition,  # Composition to run (validated here)
    run_id: str,  # Run UUID (assigned by the queue)
) -> CompositionRun:  # Fresh run record with derived topology
    """Validate a composition and build its run record."""
    deps = validate_composition(comp)
    dependents: Dict[str, Set[str]] = defaultdict(set)
    for nid, ds in deps.items():
        for d in ds:
            dependents[d].add(nid)
    return CompositionRun(
        id=run_id,
        composition=comp,
        deps=deps,
        dependents=dict(dependents),
        nodes_by_id={n.id: n for n in comp.nodes},
        node_runs={n.id: CompositionNodeRun(node_id=n.id) for n in comp.nodes},
    )


# SG-15: curate __all__ to the class + value + free-function surface. The
# attached CompositionRun methods (ready_nodes, record_started, record_result,
# skip_dependents, all_terminal, derive_terminal_status, results_by_node)
# remain accessible as CompositionRun.method(); they are removed from the
# module's star-import surface because invoking them standalone fails (they
# expect `self`). This override runs after nbdev's auto-__all__ and wins by
# being last.
