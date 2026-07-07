"""Typed exception hierarchy + JobError dataclass + default classification of bare Python exceptions. The substrate's CR-5 implementation per the 2026-05-19 substrate audit."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import ClassVar, List, Literal, Optional


class CapabilityError(Exception):
    """Base for substrate-recognized capability exceptions.
    
    Subclasses declare a `category` and `default_retriable` ClassVar so the
    JobQueue + scheduler can route the failure without sniffing exception
    text. Bare Python exceptions raised by capability code go through
    `map_bare_exception_to_job_error` to acquire a default category.
    """
    category: ClassVar[Literal['user_input', 'transient', 'resource', 'fatal']]
    default_retriable: ClassVar[bool]


class CapabilityInputError(CapabilityError):
    """User-fixable error: bad config, invalid argument, missing file.
    
    Like the other category bases (`CapabilityTransientError`,
    `CapabilityResourceError`, `CapabilityFatalError`), it extends only
    `CapabilityError`; the right reader intent is `except CapabilityInputError:`
    (or the broader `except CapabilityError:`).
    """
    category: ClassVar[Literal['user_input']] = 'user_input'
    default_retriable: ClassVar[bool] = True
    
    def __init__(
        self,
        message: str,  # Human-readable description
        *,
        fields_invalid: Optional[List[str]] = None,  # Names of inputs that failed validation
    ):
        super().__init__(message)
        self.fields_invalid: List[str] = list(fields_invalid) if fields_invalid else []


class CapabilityTransientError(CapabilityError):
    """Temporary failure: timeout, network blip, brief resource contention.
    
    Substrate / JobQueue may retry on its own initiative. Capability authors raise
    this when they know the failure is recoverable.
    """
    category: ClassVar[Literal['transient']] = 'transient'
    default_retriable: ClassVar[bool] = True
    
    def __init__(
        self,
        message: str,  # Human-readable description
        *,
        retry_after_seconds: Optional[float] = None,  # Hint for backoff strategies
    ):
        super().__init__(message)
        self.retry_after_seconds: Optional[float] = retry_after_seconds


class CapabilityResourceError(CapabilityError):
    """Resource exhaustion: GPU VRAM, system RAM, disk full.
    
    JobQueue's reactive-eviction flow (CR-7) routes resource errors to retry
    after attempting to free the named resource. Capability authors set
    `resource_shortfall` so the substrate knows what to evict.
    """
    category: ClassVar[Literal['resource']] = 'resource'
    default_retriable: ClassVar[bool] = True
    
    def __init__(
        self,
        message: str,  # Human-readable description
        *,
        resource_shortfall: Optional["ResourceShortfall"] = None,  # Quantitative gap
    ):
        super().__init__(message)
        self.resource_shortfall: Optional["ResourceShortfall"] = resource_shortfall


class CapabilityFatalError(CapabilityError):
    """Bug / irrecoverable state. The capability cannot complete this job; retrying won't help.
    
    Capability authors raise this when they know the failure is permanent for the
    given inputs. The substrate does NOT retry fatal errors.
    """
    category: ClassVar[Literal['fatal']] = 'fatal'
    default_retriable: ClassVar[bool] = False


class CapabilityDisabledError(CapabilityInputError):
    """JobQueue / execute_capability rejected: the capability is currently disabled.
    
    User-fixable (re-enable the capability). Raised by CR-2's enable/disable
    wiring once that lands.
    """
    
    def __init__(self, capability_name: str):
        super().__init__(f"Capability {capability_name!r} is disabled")
        self.capability_name = capability_name


class CapabilityNotLoadedError(CapabilityFatalError):
    """Caller submitted to a capability that was never loaded.
    
    Fatal category because this is a programmer / orchestration bug, not a
    user-fixable condition. The right reader intent is
    `except CapabilityNotLoadedError:` (or the broader `except CapabilityError:`).
    """
    
    def __init__(self, capability_name: str):
        super().__init__(f"Capability {capability_name!r} is not loaded")
        self.capability_name = capability_name


class CapabilityTimeoutError(CapabilityTransientError):
    """A per-job timeout fired before the capability finished.
    
    Transient category — retry may succeed if the slow operation completes faster
    next time. Carries `retry_after_seconds` from `CapabilityTransientError`.
    Raised by SG-14's per-job timeout primitive when that lands.
    """
    
    def __init__(
        self,
        capability_name: str,
        timeout_seconds: float,
        *,
        retry_after_seconds: Optional[float] = None,
    ):
        super().__init__(
            f"Capability {capability_name!r} timed out after {timeout_seconds:.1f}s",
            retry_after_seconds=retry_after_seconds,
        )
        self.capability_name = capability_name
        self.timeout_seconds = timeout_seconds


class CapabilityCancelledError(CapabilityTransientError):
    """Cooperative cancellation signal raised from `ToolCapability.check_cancel()`.
    
    Anchors under `CapabilityTransientError` because cancellation is in-principle
    re-runnable — a future attempt with the same inputs won't auto-fail if the
    cancel flag isn't set. But `default_retriable` is False: cancellation was
    a deliberate operator action, so the substrate should NOT auto-retry.
    Job-monitor / JobQueue render cancelled jobs with their own state
    (separate from "failed"); the JobError category remains `transient` so
    consumers reading the typed taxonomy can group recoverable signals.
    
    Capability authors raise this implicitly via `self.check_cancel()` inside
    `execute()`; substrate sets the underlying `_cancel_requested` flag via
    `cancel()`. See CR-4's cancellation primitives for the cooperative-cancel
    protocol.
    """
    default_retriable: ClassVar[bool] = False
    
    def __init__(self, capability_name: str):
        super().__init__(f"Capability {capability_name!r} cancelled by operator")
        self.capability_name = capability_name


class WorkerOOMError(CapabilityResourceError):
    """The worker subprocess died with a kill-signal during an active execute call.
    
    CR-7 Track A — substrate-side OOM detection: when an HTTP call to the worker
    faults and the subprocess has died with `returncode == -signal.SIGKILL` (or
    the platform equivalent), the substrate raises this. The kernel OOM-killer
    is the most common cause of SIGKILL during normal execute paths, so the
    substrate treats SIGKILL-during-call as "assume OOM" and surfaces a typed
    resource error for the reactive retry path.
    
    `resource_shortfall` is `None` for Track A — the substrate only saw "worker
    died from kill-signal" and has no per-resource needed/available numbers.
    Track B (per SG-47's sub-task: capability-side wrapping of `torch.cuda.OutOfMemoryError`
    et al.) raises `CapabilityResourceError` directly with a populated
    `ResourceShortfall` because the capability had the context. Both land at the
    same `except CapabilityResourceError` site in CR-7's reactive retry loop.
    
    `process_returncode` carries the observed exit code for debugging /
    classification (e.g. operators can distinguish kernel-OOM SIGKILL from
    other signals if they read it). Defaults to `None` for callers that don't
    have it on hand.
    """
    
    def __init__(
        self,
        capability_name: str,
        *,
        process_returncode: Optional[int] = None,
        message: Optional[str] = None,
    ):
        rc_part = f" (returncode={process_returncode})" if process_returncode is not None else ""
        super().__init__(
            message or f"Worker for capability {capability_name!r} died from kill-signal{rc_part}; assuming OOM",
            resource_shortfall=None,  # Track A: substrate has no needed/available
        )
        self.capability_name = capability_name
        self.process_returncode = process_returncode


class CapabilityConfigError(CapabilityInputError):
    """Unknown / invalid keys in a config dict against a capability's config schema.
    
    Reparented from `cjm_substrate.utils.validation` (Wave 2 / SG-8) under
    CR-5; SG-48 later dropped the taxonomy's last ValueError base, so this is
    catchable as `CapabilityInputError` / `CapabilityError` only — never as a
    bare `except ValueError:`. `config_class_name` is the dataclass /
    capability name whose schema was violated.
    """
    
    def __init__(
        self,
        message: str,  # Human-readable description
        *,
        fields_invalid: Optional[List[str]] = None,  # Canonical: list of bad config keys
        config_class_name: str = "",  # Dataclass / capability name for the schema
    ):
        super().__init__(message, fields_invalid=fields_invalid)
        self.config_class_name = config_class_name


@dataclass
class ResourceShortfall:
    """Quantitative gap between what a capability needed and what was available."""
    resource: Literal['gpu_vram_mb', 'system_ram_mb', 'disk_mb']  # Which resource
    needed: float  # Amount the capability reported it needed
    available: float  # Amount actually available when the failure occurred


class TracebackPolicy(str, Enum):
    """How much exception detail the substrate records on a JobError.

    FULL is what dev mode wants; REPR_ONLY / NONE are opt-outs for
    security-sensitive multi-user deployments."""
    FULL = "full"  # Default dev-mode: include traceback
    REPR_ONLY = "repr_only"  # Just the exception repr; no traceback
    NONE = "none"  # Only category + message


@dataclass
class JobError:
    """Structured failure summary recorded on a completed Job.
    
    Populated by the JobQueue when a capability execution fails (CR-6 owns the
    population logic; CR-5 owns the shape). Sufficient for UI to render a
    failure card + retry affordance without re-running the capability.
    """
    category: Literal['user_input', 'transient', 'resource', 'fatal']
    message: str  # Human-readable error message
    retriable: bool  # Whether the substrate considers this safe to auto-retry
    original_exc_repr: str  # repr(exc) of the original exception
    traceback: Optional[str] = None  # Full traceback per TracebackPolicy
    retry_after_seconds: Optional[float] = None  # Backoff hint from CapabilityTransientError
    fields_invalid: Optional[List[str]] = None  # From CapabilityInputError subclasses
    resource_shortfall: Optional[ResourceShortfall] = None  # From CapabilityResourceError
    capability_name: Optional[str] = None  # Name of the capability that raised
    capability_instance_id: Optional[str] = None  # Per CR-10 multi-instance support
    occurred_at: Optional[datetime] = None  # When the failure was recorded


_BARE_EXCEPTION_CATEGORY_MAP: "dict[type, Literal['user_input', 'transient', 'resource', 'fatal']]" = {
    # user_input: caller passed bad data, the substrate knows how to surface this to the operator
    FileNotFoundError: 'user_input',
    NotADirectoryError: 'user_input',
    IsADirectoryError: 'user_input',
    PermissionError: 'user_input',
    KeyError: 'user_input',
    ValueError: 'user_input',
    TypeError: 'user_input',
    # transient: retry may succeed
    TimeoutError: 'transient',
    ConnectionError: 'transient',
    InterruptedError: 'transient',
    BlockingIOError: 'transient',
    # resource: out of memory / disk
    MemoryError: 'resource',
    OSError: 'transient',  # broad; FileNotFoundError etc. above will match first via MRO
}
_CATEGORY_RETRIABLE_DEFAULTS: "dict[str, bool]" = {
    'user_input': True,
    'transient': True,
    'resource': True,
    'fatal': False,
}


def classify_exception(
    exc: BaseException  # The exception to classify
) -> "Literal['user_input', 'transient', 'resource', 'fatal']":  # Category
    """Return the substrate category for any exception.
    
    CapabilityError subclasses report their own declared `category`. Bare Python
    exceptions are mapped via `__mro__` walk against `_BARE_EXCEPTION_CATEGORY_MAP`;
    the first ancestor in the table wins. Unrecognized exceptions classify as
    `fatal` (don't auto-retry the unknown).
    """
    if isinstance(exc, CapabilityError):
        return exc.category
    for ancestor in type(exc).__mro__:
        if ancestor in _BARE_EXCEPTION_CATEGORY_MAP:
            return _BARE_EXCEPTION_CATEGORY_MAP[ancestor]
    return 'fatal'


def map_bare_exception_to_job_error(
    exc: BaseException,  # The raised exception
    *,
    capability_name: Optional[str] = None,  # Name of the capability that raised
    capability_instance_id: Optional[str] = None,  # Per CR-10
    traceback_policy: TracebackPolicy = TracebackPolicy.FULL,  # How much detail to record
    occurred_at: Optional[datetime] = None,  # Override; defaults to datetime.now(timezone.utc)
) -> JobError:
    """Convert any exception into a structured `JobError`.
    
    CapabilityError subclasses contribute their category-specific structured data
    (`fields_invalid` for input errors, `resource_shortfall` for resource errors,
    `retry_after_seconds` for transient errors). Bare exceptions get the
    default category-based retriable flag and no structured side-channel.
    """
    category = classify_exception(exc)
    
    if isinstance(exc, CapabilityError):
        retriable = exc.default_retriable
    else:
        retriable = _CATEGORY_RETRIABLE_DEFAULTS[category]
    
    if traceback_policy is TracebackPolicy.FULL:
        import traceback as _tb
        tb_str = _tb.format_exception(type(exc), exc, exc.__traceback__)
        tb = "".join(tb_str)
    else:
        tb = None
    
    fields_invalid = getattr(exc, 'fields_invalid', None) if isinstance(exc, CapabilityInputError) else None
    resource_shortfall = getattr(exc, 'resource_shortfall', None) if isinstance(exc, CapabilityResourceError) else None
    retry_after = getattr(exc, 'retry_after_seconds', None) if isinstance(exc, CapabilityTransientError) else None
    
    return JobError(
        category=category,
        message=str(exc) if traceback_policy is not TracebackPolicy.NONE else "",
        retriable=retriable,
        original_exc_repr=repr(exc),
        traceback=tb,
        retry_after_seconds=retry_after,
        fields_invalid=fields_invalid,
        resource_shortfall=resource_shortfall,
        capability_name=capability_name,
        capability_instance_id=capability_instance_id,
        # datetime.now(timezone.utc) — `datetime.utcnow()` is deprecated in
        # Python 3.12+ and returns a naive datetime; the timezone-aware form
        # is the canonical 3.12+ replacement and survives the eventual removal.
        occurred_at=occurred_at or datetime.now(timezone.utc),
    )
