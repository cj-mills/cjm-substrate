# cjm-substrate

<!-- generated from the context graph by `cjm-context-graph readme` — do not edit by hand; edit the graph (the urge to hand-edit = move it on-graph) -->

A dependency-isolated capability-composition runtime: heterogeneous tools run in their own environments behind a uniform HTTP/JSON boundary; the host composes them into workflows with resource-aware scheduling and threads provenance through a context graph.

## Modules

- **`cjm_substrate.bootstrap`** — One-call factory that assembles a CapabilityManager + JobQueue + capability
- **`cjm_substrate.cli`** — CLI tool for declarative capability management.
- **`cjm_substrate.core._telemetry`** — Shared GPU/CPU attribution helpers used by both JobQueue._sample_resource_snapshot (CR-6 Stage 3) and CapabilityManager._record_sample_safe (CR-7).
- **`cjm_substrate.core.adapter`** — The typed-task half of the capability-unit fracture (pass-2 Thread 3) —
- **`cjm_substrate.core.adapter_manifest`** — The ADAPTER unit's registration manifest + the surface-based compatibility matcher (CR-17 pt 2, stage 4). Pass-2 Thread 3: registration/discovery = per-unit manifests generated in-env and found by discover_manifests(); compatibility is DERIVED, not declared — the capability records only its structural surface, the adapter declares its protocol (recorded here as member names + parameter lists), and the substrate matches manifest-vs-manifest. Works against UNLOADED capabilities with zero protocol imports host-side.
- **`cjm_substrate.core.capability`** — The tool-capability interface — the manage-the-tool half of the capability-unit fracture (pass-2 Thread 3): identity, lifecycle, config, cancellation, and observability for a tool running in a worker process, serving both concrete capabilities (in workers) and remote proxies (in the host). The task channel is deliberately NOT here: typed task contracts live on task adapters (core.adapter + the per-task cjm-<task>-adapter-interface libraries) and results cross the worker boundary through the typed wire layer (core.wire); execute_stream's transitional default remains for fused-era capabilities only. Also home to the CR-12 worker-env contract (EnvVarSpec + the Q1-A template vocabulary), the SG-44/T28 action-dispatcher convention, and the pass-2 Thread-3 structural-surface derivation.
- **`cjm_substrate.core.config`** — Project-level configuration for paths, runtime settings, and environment management.
- **`cjm_substrate.core.config_store`** — Persistent storage for per-capability configuration (with enabled flag).
- **`cjm_substrate.core.diagnostics_store`** — CR-14 (stage 7): the disposable diagnostic-narrative class. Worker-written
- **`cjm_substrate.core.empirical_store`** — Persistent store for empirically-observed resource usage per (instance_id, config_hash) pair. CR-7's data foundation — record_sample is called from CapabilityManager.execute_capability* finally blocks; aggregates feed eviction-candidate selection + future UI hints + cost-aware retry decisions.
- **`cjm_substrate.core.errors`** — Typed exception hierarchy + JobError dataclass + default classification of bare Python exceptions. The substrate's CR-5 implementation per the 2026-05-19 substrate audit.
- **`cjm_substrate.core.journal_store`** — CR-14 (stage 7): the durable account-of-action. One substrate-derived,
- **`cjm_substrate.core.manager`** — Capability discovery, loading, and lifecycle management via process isolation.
- **`cjm_substrate.core.manifest_format`** — Typed parser + writer for the nested v2.0 manifest layout (2026-05-19 substrate audit, CR-8).
- **`cjm_substrate.core.metadata`** — Data structures for capability metadata.
- **`cjm_substrate.core.platform`** — Cross-platform utilities for process management, path handling, and system detection (Linux, macOS, Windows).
- **`cjm_substrate.core.ports`** — Capability compositions as DAGs of invocation nodes with typed input/output
- **`cjm_substrate.core.proxy`** — Host-side bridge to isolated capability workers: RemoteCapabilityProxy implements ToolCapability but forwards every call over HTTP to a Universal Worker subprocess running in the capability's own environment. Owns worker process management (spawn with the manifest's python_path, SG-4 parent-bound listening socket / FD inheritance, suicide-pact --ppid, CR-14 stream pump + lifecycle journaling), zero-copy input transfer (FileBackedDTO -> temp files), the typed error contracts crossing the wire (409 -> CapabilityCancelledError; _job_error 500 bodies and stream terminal chunks -> typed exceptions; CR-7 Track A worker-death classification), and a dual sync/async interface for scripts and FastHTML hosts.
- **`cjm_substrate.core.queue`** — Resource-aware multi-lane job queue for capability execution (stage 3 / CR-16 rework).
- **`cjm_substrate.core.scheduling`** — Resource scheduling policies for capability execution.
- **`cjm_substrate.core.secret_store`** — CR-12: project-local secret storage for API-based capabilities (file-backed, 0600).
- **`cjm_substrate.core.wire`** — Typed data transfer at the worker boundary — the zero-copy FileBackedDTO
- **`cjm_substrate.core.worker`** — FastAPI server that runs inside isolated capability environments (the Universal Worker): dynamically loads the capability class named on the CLI, exposes the HTTP lifecycle / execute / task / monitor surface for the proxy, monitors the parent process (the suicide-pact watchdog prevents zombie workers), and reports process-subtree telemetry for resource-scheduling decisions. This module is a process ENTRYPOINT (SG-39): host code never imports it.
- **`cjm_substrate.utils.cache_paths`** — Per-(input-content, config) deterministic cache directories for capability outputs. Same (input content, action, config) always resolves to the same directory; any change to input content OR config produces a different one — no silent overwrites, no stale-artifact accumulation, and chained invalidation for capability sequences (see the cache-paths-design-provenance note for the ffmpeg-bug origin story).
- **`cjm_substrate.utils.hashing`** — Shared cryptographic hashing primitives for content integrity verification.
- **`cjm_substrate.utils.validation`** — Validation helpers for capability configuration dataclasses.

## API

### `cjm_substrate.bootstrap`

- `Pipeline` _class_ — Assembled substrate stack: manager + queue + capability bindings.
- `create_pipeline` _function_ — Assemble a CapabilityManager + JobQueue + capability bindings in one call.

### `cjm_substrate.cli`

- `generate_adapter_manifest` _function_ — CR-17 pt 2 (stage 4): introspect a task-adapter impl in-env and write its adapter manifest.
- `install_all` _function_ — Install and register all capabilities defined in capabilities.yaml.
- `list_capabilities` _function_ — List installed capabilities from manifest directory.
- `list_secrets` _function_ — List the secret KEY NAMES stored for a capability — never the values (CR-12).
- `logs_command` _function_ — Tail / follow the observability stores (CR-14).
- `main` _function_ — cjm-substrate CLI for managing isolated capability environments.
- `regenerate_manifest` _function_ — Re-run introspection for an installed capability and rewrite its manifest.
- `remove_capability` _function_ — Remove a capability's manifest and conda environment.
- `retention_command` _function_ — Apply the diagnostics retention policy now (CR-14).
- `run_cmd` _function_ — Run a shell command and stream output.
- `set_secret` _function_ — Store a capability secret in the project-local SecretStore (CR-12).
- `setup_host` _function_ — Install interface libraries in the current Python environment.
- `setup_runtime` _function_ — Download and setup micromamba runtime for project-local mode.
- `validate_file` _function_ — SG-6 + T23: validate a manifest / capabilities.yaml / capability source.

### `cjm_substrate.core._telemetry`

- `attribute_gpu_to_worker_subtree` _function_ — Attribute GPU memory across the worker's process subtree.

### `cjm_substrate.core.adapter`

- `TaskAdapter` _class_ — Base for task adapters — the typed-task half of the capability-unit

### `cjm_substrate.core.adapter_manifest`

- `AdapterManifest` _class_ — A discovered ADAPTER unit (CR-17 pt 2) — the registration record for one
- `adapter_manifest_from_dict` _function_ — Reconstruct an `AdapterManifest` from its on-disk JSON shape.
- `is_adapter_manifest` _function_ — Route a manifest file by the `unit` discriminator (capability manifests
- `match_protocol_against_surface` _function_ — Surface-based compatibility (pass-2 Thread 3) — host-side, manifest-vs-

### `cjm_substrate.core.capability`

- `ConfigOption` _class_ — CR-11: one live option for a dynamic config field, with optional metadata.
- `EnvVarSpec` _class_ — CR-12: one entry of a capability's spawn-time worker-environment contract.
- `FieldOptions` _class_ — CR-11: the live option domain for one dynamic config field.
- `ToolCapability` _class_ — Tool-capability interface: manage the tool/worker — identity, lifecycle,
- `capability_action` _function_ — Marker decorator tagging a capability method as the handler for `action_name`.
- `collect_capability_actions` _function_ — Collect action names from `@capability_action`-decorated methods on `cls`.
- `derive_structural_surface` _function_ — Record a capability class's structural surface by pure self-introspection.
- `expand_worker_env_template` _function_ — Substitute `${VAR}` placeholders in `template` using `placeholders`.
- `template_check_placeholders` _function_ — Return the set of placeholder names referenced by a worker-env template.

### `cjm_substrate.core.config`

- `CJMConfig` _class_ — Main configuration for cjm-substrate.
- `CondaType` _class_ — Type of conda implementation to use.
- `RuntimeConfig` _class_ — Runtime environment configuration.
- `RuntimeMode` _class_ — Runtime mode for the capability system.
- `SubstrateConfig` _class_ — Substrate behavior toggles.
- `get_config` _function_ — Get current config (loads defaults if not set).
- `load_config` _function_ — Load config with layered resolution (CLI > env vars > yaml > defaults).
- `reset_config` _function_ — Reset to unloaded state (for testing).
- `set_config` _function_ — Set current config (called by CLI callback).

### `cjm_substrate.core.config_store`

- `CapabilityConfigRecord` _class_ — Persisted state for a capability: config dict + enabled flag.
- `CapabilityConfigStore` _class_ — Protocol for persisting per-capability `CapabilityConfigRecord` across sessions.
- `LocalCapabilityConfigStore` _class_ — SQLite-backed default implementation of `CapabilityConfigStore`.
- `delete` _function_ — Remove the record for a capability.
- `get` _function_ — Fetch the record for a capability.
- `list_all` _function_ — Return all stored records keyed by capability name.
- `set` _function_ — Persist a record. Stamps `updated_at` to the current time.

### `cjm_substrate.core.diagnostics_store`

- `DiagnosticRecord` _class_ — One structured worker log record (CR-14 diagnostics class).
- `DiagnosticsLogHandler` _class_ — Worker-side logging handler writing `DiagnosticRecord`s (CR-14).
- `DiagnosticsStore` _class_ — Protocol for the disposable diagnostic-narrative store (CR-14).
- `LocalDiagnosticsStore` _class_ — SQLite-backed default `DiagnosticsStore` (CR-14).
- `StreamChunk` _class_ — One raw stdout/stderr line the host pump captured (death-rattle floor).
- `append_chunk` _function_ — Persist one raw stream line.
- `append_record` _function_ — Persist one structured record.
- `apply_retention` _function_ — Retention as a QUERY (the CR-14 reframe's mechanical payoff).
- `install_worker_diagnostics` _function_ — Configure worker-process logging (replaces the old `basicConfig`).
- `normalize_stream_line` _function_ — Collapse CR progress frames to the final frame; drop empty results.
- `query_chunks` _function_ — Raw stream read, session-scoped.
- `query_records` _function_ — Filtered structured-record read.

### `cjm_substrate.core.empirical_store`

- `EmpiricalResourceRecord` _class_ — Aggregated empirical resource profile for a (instance_id, config_hash) pair.
- `EmpiricalResourceStore` _class_ — Protocol for persisting empirically-observed resource usage.
- `LocalEmpiricalResourceStore` _class_ — SQLite-backed default implementation of `EmpiricalResourceStore`.
- `ResourceSample` _class_ — Single observation captured after an execute call completes.
- `compute_config_hash` _function_ — CR-7: hash a capability instance's effective config for empirical-record keying.

### `cjm_substrate.core.errors`

- `CapabilityCancelledError` _class_ — Cooperative cancellation signal raised from `ToolCapability.check_cancel()`.
- `CapabilityConfigError` _class_ — Unknown / invalid keys in a config dict against a capability's config schema.
- `CapabilityDisabledError` _class_ — JobQueue / execute_capability rejected: the capability is currently disabled.
- `CapabilityError` _class_ — Base for substrate-recognized capability exceptions.
- `CapabilityFatalError` _class_ — Bug / irrecoverable state. The capability cannot complete this job; retrying won't help.
- `CapabilityInputError` _class_ — User-fixable error: bad config, invalid argument, missing file.
- `CapabilityNotLoadedError` _class_ — Caller submitted to a capability that was never loaded.
- `CapabilityResourceError` _class_ — Resource exhaustion: GPU VRAM, system RAM, disk full.
- `CapabilityTimeoutError` _class_ — A per-job timeout fired before the capability finished.
- `CapabilityTransientError` _class_ — Temporary failure: timeout, network blip, brief resource contention.
- `JobError` _class_ — Structured failure summary recorded on a completed Job.
- `ResourceShortfall` _class_ — Quantitative gap between what a capability needed and what was available.
- `TracebackPolicy` _class_ — How much exception detail the substrate records on a JobError.
- `WorkerOOMError` _class_ — The worker subprocess died with a kill-signal during an active execute call.
- `classify_exception` _function_ — Return the substrate category for any exception.
- `map_bare_exception_to_job_error` _function_ — Convert any exception into a structured `JobError`.

### `cjm_substrate.core.journal_store`

- `JournalEvent` _class_ — One durable observability record (CR-14).
- `JournalStore` _class_ — Protocol for the durable account-of-action (CR-14).
- `LocalJournalStore` _class_ — SQLite-backed default `JournalStore` (CR-14).
- `SubstrateEventType` _class_ — Journal vocabulary beyond the job-scoped `JobEventType` set (CR-14).
- `append` _function_ — Persist one event; sets and returns `event.seq`.
- `count` _function_ — Total journal rows (volume regression checks).
- `query` _function_ — Filtered read; all filters AND-combined.
- `terminal_state_events` _function_ — The durable job history (`_history` migration rider): terminal

### `cjm_substrate.core.manager`

- `CapabilityBinding` _class_ — Pre-bound view of a single capability through a shared CapabilityManager.
- `CapabilityManager` _class_ — Manages capability discovery, loading, and lifecycle via process isolation.

### `cjm_substrate.core.manifest_format`

- `CodeSection` _class_ — Code-derived facts refreshed by `cjm-ctl regenerate-manifest`.
- `DriftTracking` _class_ — Witness hashes for drift detection.
- `InstallSection` _class_ — Deployment-specific facts populated at install time.
- `ManifestV2` _class_ — Top-level v2.0 manifest with four named sections plus `format_version`.
- `compute_config_schema_hash` _function_ — Hash a JSON Schema with stable canonicalization.
- `compute_structural_surface_hash` _function_ — Hash a structural surface with stable canonicalization.
- `load_manifest` _function_ — Load a manifest file and return a typed `ManifestV2`.
- `manifest_to_dict` _function_ — Serialize a `ManifestV2` to a v2.0 dict.
- `write_manifest` _function_ — Serialize a `ManifestV2` to disk in v2.0 nested layout (indent=2).

### `cjm_substrate.core.metadata`

- `CapabilityInstance` _class_ — Per-instance runtime state for a loaded capability (CR-10 multi-instance).
- `CapabilityLoadSpec` _class_ — One entry in `CapabilityManager.load_capabilities_concurrent`'s batch input (CR-10).
- `CapabilityMeta` _class_ — Metadata about a capability.
- `ResourceRequirements` _class_ — Binary hard-facts about what a capability needs to run (Phase 5a).

### `cjm_substrate.core.platform`

- `build_conda_command` _function_ — Build a complete conda/mamba/micromamba command.
- `conda_env_exists` _function_ — Check if a conda environment exists (cross-platform).
- `download_micromamba` _function_ — Download and extract micromamba binary to the specified path.
- `ensure_runtime_available` _function_ — Check if the configured conda/micromamba runtime is available.
- `get_conda_command` _function_ — Get the conda/mamba/micromamba base command with prefix args for local mode.
- `get_current_platform` _function_ — Get current platform string for manifest filtering.
- `get_micromamba_binary_path` _function_ — Get the configured micromamba binary path for the current platform.
- `get_micromamba_download_url` _function_ — Get the micromamba download URL for the specified or current platform.
- `get_popen_isolation_kwargs` _function_ — Return kwargs for process isolation in subprocess.Popen.
- `get_python_in_env` _function_ — Get the Python executable path for a conda environment.
- `is_apple_silicon` _function_ — Check if running on Apple Silicon Mac (for MPS detection).
- `is_linux` _function_ — Check if running on Linux.
- `is_macos` _function_ — Check if running on macOS.
- `is_windows` _function_ — Check if running on Windows.
- `run_shell_command` _function_ — Run a shell command cross-platform.
- `terminate_process` _function_ — Terminate a subprocess + its entire process subtree (grandchildren, etc).
- `terminate_self` _function_ — Terminate the current process (for worker suicide pact).

### `cjm_substrate.core.ports`

- `Composition` _class_ — A static DAG of capability-invocation nodes, submitted as one unit.
- `CompositionBindingError` _class_ — An `OutputRef` could not be resolved against the producer's recorded
- `CompositionNode` _class_ — One capability invocation in a composition.
- `CompositionNodeRun` _class_ — Live state of one node within a composition run.
- `CompositionRun` _class_ — Tracks a submitted composition through execution (lives in
- `CompositionValidationError` _class_ — A composition failed submit-time validation (duplicate ids, unresolved
- `NodeState` _class_ — State of one composition node (and, for the terminal subset, of a
- `OutputRef` _class_ — Binding marker: this kwarg's value comes from an upstream node's result.
- `extract_output_field` _function_ — Extract a field from an upstream result for binding into a kwarg.
- `new_composition_run` _function_ — Validate a composition and build its run record.
- `resolve_node_kwargs` _function_ — Materialize a node's kwargs by resolving its `OutputRef` markers.
- `validate_composition` _function_ — Validate a composition and return its derived dependency map.

### `cjm_substrate.core.proxy`

- `RemoteCapabilityProxy` _class_ — Proxy that forwards capability calls to an isolated Worker subprocess.

### `cjm_substrate.core.queue`

- `CancelPhase` _class_ — Phase of a cancellation in progress (CR-6 + CR-4 pairing).
- `Job` _class_ — A queued capability execution request (CR-6 reshape; stage-3 composition
- `JobEvent` _class_ — A push-based job event (CR-6; stage-3 composition tags).
- `JobEventType` _class_ — Push-based job event types (CR-6; stage-3 composition rework; CR-14
- `JobQueue` _class_ — Resource-aware multi-lane job queue with journal-primary observability
- `JobQueueDependencies` _class_ — Substrate dependencies the JobQueue requires (CR-6 + stage 3).
- `JobStatus` _class_ — Status of a job in the queue.
- `QueueStats` _class_ — Aggregate counts returned by `JobQueue.get_stats()` (CR-6).
- `ResourceSnapshot` _class_ — Point-in-time resource usage for one job (CR-6 Stage 3).

### `cjm_substrate.core.scheduling`

- `PermissiveScheduler` _class_ — Scheduler that allows all executions (Default / Dev Mode).
- `ResourceScheduler` _class_ — Abstract base class for resource allocation policies.

### `cjm_substrate.core.secret_store`

- `LocalSecretStore` _class_ — File-backed default `SecretStore` (0600 JSON under `secrets_dir`).
- `SecretStore` _class_ — Protocol for resolving per-capability secrets (API keys, tokens).
- `delete_secret` _function_ — Remove a secret, pruning now-empty capability/scope containers.
- `get_secret` _function_ — Resolve a secret value.
- `list_keys` _function_ — Return the names of secrets stored for a capability (never the values).
- `set_secret` _function_ — Persist a secret value.

### `cjm_substrate.core.wire`

- `CallEnvelope` _class_ — Substrate-owned per-call identity + control block (CR-14 / CR-15).
- `FileBackedDTO` _class_ — Protocol for Data Transfer Objects that serialize to disk for zero-copy transfer.
- `begin_account_capture` _function_ — Start a fresh account list for the current call span (worker endpoint
- `drain_accounts` _function_ — Return + clear the current span's recorded accounts ([] outside a span
- `flat_from_dict` _function_ — Default reconstruction for FLAT wire DTOs (no nested-DTO fields).
- `get_call_envelope` _function_ — The current call envelope, or None outside any call span.
- `record_account` _function_ — Record one substrate-family account for the current call span.
- `reset_call_envelope` _function_ — Restore the prior envelope (always pair with `set_call_envelope` in finally).
- `set_call_envelope` _function_ — Set the current call envelope; returns the token for `reset_call_envelope`.
- `wire_decode` _function_ — Reconstruct a typed result from its tagged envelope (host side).
- `wire_encode` _function_ — Wrap a registered wire DTO in its tagged envelope (worker side).
- `wire_type` _function_ — Register a dataclass as a typed wire DTO under `kind`.

### `cjm_substrate.core.worker`

- `EnhancedJSONEncoder` _class_ — JSON encoder that handles dataclasses and other common types.
- `create_app` _function_ — Create FastAPI app that hosts the specified capability.
- `parent_monitor` _function_ — Monitor parent process and terminate self if parent dies.
- `run_worker` _function_ — CLI entry point for running the worker.

### `cjm_substrate.utils.cache_paths`

- `cache_dir_for_config` _function_ — Return (and optionally create) a per-(input-content, config) cache directory.
- `list_cache_entries` _function_ — Enumerate all per-config cache directories for a given (input, action).
- `prune_cache_for_input` _function_ — Delete per-config cache directories for `(input, action)`, optionally

### `cjm_substrate.utils.hashing`

- `hash_bytes` _function_ — Compute a hash of byte content.
- `hash_dict_canonical` _function_ — Hash a dict via canonical JSON encoding.
- `hash_file` _function_ — Stream-hash a file without loading it entirely into memory.
- `verify_hash` _function_ — Verify content against an expected hash string.

### `cjm_substrate.utils.validation`

- `config_to_dict` _function_ — Convert a configuration dataclass instance to a dictionary.
- `dataclass_to_jsonschema` _function_ — Convert a dataclass to a JSON schema for form generation.
- `dict_to_config` _function_ — Create a configuration dataclass instance from a dictionary.
- `extract_defaults` _function_ — Extract default values from a configuration dataclass type.
- `validate_config` _function_ — Validate all fields in a configuration dataclass against their metadata constraints.
- `validate_field_value` _function_ — Validate a value against field metadata constraints.

## Dependencies

**Depends on:** `fastapi`, `fastcore`, `httpx`, `psutil`, `pyyaml`, `typer`, `uvicorn`
**Used by:** `cjm-capability-demucs`, `cjm-capability-ffmpeg`, `cjm-capability-graph-sqlite`, `cjm-capability-monitor-nvidia`, `cjm-capability-primitives`, `cjm-capability-qwen3-forced-aligner`, `cjm-capability-silero-vad`, `cjm-capability-voxtral-hf`, `cjm-capability-whisper`, `cjm-context-graph-layer`, `cjm-context-graph-primitives`, `cjm-context-graph-projection`, `cjm-markdown-decompose-core`, `cjm-transcript-correction-core`, `cjm-transcript-correction-tui`, `cjm-transcript-decomp-core`, `cjm-transcription-adapter-interface`, `cjm-transcription-core`, `cjm-transcription-tui`, `hf-utils`, `torch-utils`
