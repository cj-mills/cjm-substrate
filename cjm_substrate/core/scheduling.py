"""Resource scheduling policies for capability execution."""

from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Dict

from cjm_substrate.core.metadata import CapabilityMeta


class ResourceScheduler(ABC):
    """Abstract base class for resource allocation policies.

    Schedulers are the POLICY half of a mechanism/policy split: capabilities
    report stats (mechanism), schedulers decide allocation (policy). The
    manager defaults to `PermissiveScheduler`; hosts inject an alternative
    through the `CapabilityManager(scheduler=...)` seam. Quantity-based
    policies (the pre-CR-7 SafetyScheduler / QueueScheduler) read manifest
    fields the CR-7 reactive-resource reframe dropped and were deleted —
    resource safety now lives in the empirical layer (the queue's multi-lane
    admission over `get_admission_profile` + `get_global_stats`, and the
    manager's reactive retry/eviction path).
    """

    @abstractmethod
    def allocate(
        self,
        capability_meta: CapabilityMeta,  # Metadata of the capability requesting resources
        stats_provider: Callable[[], Dict[str, Any]]  # Function that returns fresh stats
    ) -> bool:  # True if execution is allowed
        """Decide if a capability can start based on its requirements and system state."""
        ...

    async def allocate_async(
        self,
        capability_meta: CapabilityMeta,  # Metadata of the capability requesting resources
        stats_provider: Callable[[], Awaitable[Dict[str, Any]]]  # Async function returning stats
    ) -> bool:  # True if execution is allowed
        """Async allocation decision. Default delegates to sync allocate after fetching stats once."""
        stats = await stats_provider()
        return self.allocate(capability_meta, lambda: stats)

    @abstractmethod
    def on_execution_start(
        self,
        capability_name: str  # Name of the capability starting execution
    ) -> None:
        """Notify scheduler that a task started (to reserve resources)."""
        ...

    @abstractmethod
    def on_execution_finish(
        self,
        capability_name: str  # Name of the capability finishing execution
    ) -> None:
        """Notify scheduler that a task finished (to release resources)."""
        ...


class PermissiveScheduler(ResourceScheduler):
    """Scheduler that allows all executions (Default / Dev Mode)."""

    def allocate(
        self,
        capability_meta: CapabilityMeta,  # Metadata of the capability requesting resources
        stats_provider: Callable[[], Dict[str, Any]]  # Stats provider (ignored)
    ) -> bool:  # Always returns True
        """Allow all capability executions without checking resources."""
        return True

    def on_execution_start(
        self,
        capability_name: str  # Name of the capability starting execution
    ) -> None:
        """No-op for permissive scheduler."""
        pass

    def on_execution_finish(
        self,
        capability_name: str  # Name of the capability finishing execution
    ) -> None:
        """No-op for permissive scheduler."""
        pass
