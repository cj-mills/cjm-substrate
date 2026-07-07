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
- **`tests.test_adapter`** — TaskAdapter shape test (projected from nbs/core/adapter.ipynb cell c74f6c0e7ade
- **`tests.test_adapter_manifest`** — Adapter-manifest matcher tests (projected from nbs/core/adapter_manifest.ipynb
- **`tests.test_bootstrap`** — Bootstrap tests (projected from nbs/bootstrap.ipynb cell smoke-test at the
- **`tests.test_cache_paths`** — Cache-path tests (projected from nbs/utils/cache_paths.ipynb cell cell-tests
- **`tests.test_capability`** — ToolCapability tests (projected from nbs/core/capability.ipynb #|hide cells
- **`tests.test_cli`** — CLI validator + source-lint tests (projected from nbs/cli.ipynb hide cells
- **`tests.test_config`** — Configuration tests (projected from nbs/core/config.ipynb cells
- **`tests.test_config_store`** — Config-store tests (projected from nbs/core/config_store.ipynb cell smoke-test
- **`tests.test_diagnostics_store`** — Diagnostics-store tests (projected from nbs/core/diagnostics_store.ipynb
- **`tests.test_empirical_store`** — Empirical-store tests (projected from nbs/core/empirical_store.ipynb cells
- **`tests.test_errors`** — Error-taxonomy regression tests (projected from nbs/core/errors.ipynb cells
- **`tests.test_hashing`** — Hashing-utility tests (projected from nbs/utils/hashing.ipynb cells
- **`tests.test_journal_store`** — Journal-store tests (projected from nbs/core/journal_store.ipynb cells
- **`tests.test_manager`** — CapabilityManager tests (projected from nbs/core/manager.ipynb hide cells
- **`tests.test_manifest_format`** — Manifest-format tests (projected from nbs/core/manifest_format.ipynb cells
- **`tests.test_metadata`** — Capability-metadata tests (projected from nbs/core/metadata.ipynb cells
- **`tests.test_platform`** — Platform-utilities tests (projected from nbs/core/platform.ipynb cells
- **`tests.test_ports`** — Composition-ports tests (projected from nbs/core/ports.ipynb cells
- **`tests.test_proxy`** — RemoteCapabilityProxy tests (projected from nbs/core/proxy.ipynb #|hide
- **`tests.test_queue`** — JobQueue tests (projected from nbs/core/queue.ipynb #|hide cells at the
- **`tests.test_scheduling`** — Scheduling-policy tests (authored at the golden-reference flip of
- **`tests.test_secret_store`** — Secret-store tests (projected from nbs/core/secret_store.ipynb cell smoke-test
- **`tests.test_telemetry`** — GPU-attribution helper tests (projected from nbs/core/telemetry.ipynb cells
- **`tests.test_validation`** — Validation-utility tests (projected from nbs/utils/validation.ipynb cells
- **`tests.test_wire`** — Typed-wire-layer tests (projected from nbs/core/wire.ipynb cells c8a35529 /
- **`tests.test_worker`** — Universal Worker tests (projected from nbs/core/worker.ipynb #|hide cells at
- **`tests_manual.validate_stage7_observability_e2e`** — Stage-7 follow-up stress suite — the CR-14 observability record architecture.

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

### `tests.test_adapter`

- `test_base_protocol_slot_defaults_provisional` _function_
- `test_concrete_impl_fills_the_shape` _function_
- `test_per_task_abc_keeps_its_abstract_set` _function_

### `tests.test_adapter_manifest`

- `test_exact_prefix_and_property_matches_are_compatible` _function_
- `test_manifest_round_trip_and_kind_check` _function_
- `test_missing_method_says_no_legibly` _function_
- `test_missing_property_is_a_mismatch` _function_
- `test_param_less_old_format_falls_back_to_name_only` _function_
- `test_pre_fracture_surface_is_not_compatible_with_reason` _function_
- `test_reordered_params_are_a_mismatch` _function_

### `tests.test_bootstrap`

- `test_normalize_spec_accepts_all_three_forms` _function_
- `test_normalize_spec_rejects_bad_forms` _function_
- `test_pipeline_context_manager_starts_and_stops` _function_
- `test_pipeline_dataclass_shape` _function_

### `tests.test_cache_paths`

- `test_cache_dir_basic_determinism_and_config_keying` _function_ — Same (input, action, config) → same dir; different config → different dir.
- `test_create_false_returns_path_without_mkdir` _function_
- `test_hash_input_content_false_uses_the_path_string` _function_ — hash_input_content=False hashes the path string (URL / non-file inputs).
- `test_list_and_prune_companions` _function_
- `test_modify_in_place_changes_the_cache_key` _function_
- `test_same_stem_different_content_gets_distinct_keys` _function_ — Content hash distinguishes two same-stem files in different directories.
- `test_sanitize_stem_edge_cases` _function_
- `test_sequence_chaining_auto_invalidates_downstream` _function_ — When capability A's config changes, A's output content changes, so B's
- `test_skip_cache_bypasses_the_lookup` _function_
- `test_stat_cache_round_trip` _function_

### `tests.test_capability`

- `ApplyConfigCapability` _class_ — Capability with the clean _apply_config seam.
- `BaseDispatcher` _class_
- `DispatchCapability` _class_
- `ExtendedDispatchCapability` _class_
- `ExtendedDispatcher` _class_
- `FallbackCapability` _class_ — No _apply_config — reconfigure must fall back to initialize(new).
- `MinimalCapability` _class_ — Concrete capability satisfying abstracts; relies on CR-4 default cleanup().
- `RaisingTriggerCapability` _class_
- `TriggerCapability` _class_ — Capability that opts into the declarative RELOAD_TRIGGER pattern.
- `WhisperTestConfig` _class_ — Config dataclass with two RELOAD_TRIGGER-tagged fields sharing a trigger.
- `test_cancel_callbacks_fire_in_order_every_time` _function_
- `test_cancel_flag_and_check_cancel` _function_
- `test_cancel_signal_to_registers_and_deregisters` _function_
- `test_capability_action_tags_without_wrapping` _function_
- `test_cleanup_and_prefetch_are_optional_no_ops` _function_
- `test_collect_capability_actions_walks_mro` _function_
- `test_config_options_default_is_empty` _function_
- `test_config_options_override_and_asdict_round_trip` _function_
- `test_derive_structural_surface` _function_
- `test_dispatch_to_action_routes_and_forwards_kwargs` _function_
- `test_dispatch_unknown_action_raises_typed` _function_
- `test_dispatch_walks_mro_for_inherited_handlers` _function_
- `test_env_var_spec_flavors_and_round_trip` _function_
- `test_execute_stream_default_wraps_execute` _function_
- `test_fields_that_changed` _function_
- `test_heartbeat_advances_status_tuple` _function_
- `test_heartbeat_preserves_explicit_progress` _function_
- `test_heartbeat_thread_terminates_on_both_exits` _function_
- `test_misbehaving_cancel_callback_is_skipped` _function_
- `test_reconfigure_delegates_to_triggers` _function_
- `test_reconfigure_two_phase_contract` _function_
- `test_reconfigure_with_triggers` _function_
- `test_report_progress_fast_path_without_lock` _function_
- `test_report_progress_safe_under_concurrency` _function_
- `test_report_progress_uses_lock_inside_heartbeat` _function_
- `test_template_check_placeholders_validates_vocabulary` _function_
- `test_template_is_single_pass_non_recursive` _function_
- `test_template_missing_value_is_operator_error` _function_
- `test_template_static_defaults_pass_through` _function_
- `test_template_substitutes_placeholders` _function_
- `test_template_unknown_placeholder_raises_with_context` _function_

### `tests.test_cli`

- `test_adapters_entries_validated` _function_ — Stage 6 J10: well-formed adapters pass; malformed flagged loudly.
- `test_bad_config_schema_shape_flagged` _function_
- `test_bad_drift_hash_type_flagged` _function_
- `test_bad_env_vars_type_flagged` _function_
- `test_basicconfig_lint_directory_scan` _function_ — force=True is an ERROR, plain basicConfig a WARNING, clean files
- `test_basicconfig_lint_single_file_comment_only` _function_ — Single-file mode; comment-only mentions don't fire.
- `test_clean_manifest_produces_no_warnings` _function_
- `test_duplicate_capability_names_flagged` _function_
- `test_format_detection_from_extension` _function_
- `test_missing_capabilities_key_flagged` _function_
- `test_missing_code_section_flagged` _function_
- `test_missing_env_creation_source_flagged` _function_
- `test_missing_format_version_rejects_loud` _function_ — The v1.0 flat shim was removed at SG-48; no format_version now rejects.
- `test_missing_install_python_path_flagged` _function_
- `test_missing_required_code_field_flagged` _function_
- `test_non_dict_root_rejected` _function_
- `test_resources_type_check_on_nested_layout` _function_
- `test_t23_minimal_manifest_valid` _function_
- `test_unrecognized_format_version_rejects_loud` _function_
- `test_v12_dropped_quantitative_resource_field_warns` _function_
- `test_v1_whitespace_only_required_field_rejected` _function_ — V1: whitespace-only description now rejected (was: only "" rejected).
- `test_v4_single_element_enum_warns_not_errors` _function_
- `test_valid_capabilities_yaml_passes` _function_
- `test_valid_v2_manifest_passes` _function_
- `test_worker_env_allowed_placeholders_validate_clean` _function_ — Includes the T31-renamed CJM_CAPABILITY_DATA_DIR.
- `test_worker_env_must_be_list` _function_
- `test_worker_env_old_cjm_data_dir_placeholder_rejected` _function_ — T31: the OLD ${CJM_DATA_DIR} placeholder is no longer in the vocabulary.
- `test_worker_env_unknown_placeholder_is_error` _function_

### `tests.test_config`

- `test_cli_override_takes_effect_and_derived_paths_follow` _function_
- `test_conda_binary_path_defaults_under_prefix_and_none_without` _function_
- `test_conda_binary_path_prefers_platform_binaries_map` _function_
- `test_dataclass_creation` _function_
- `test_default_configuration_paths` _function_
- `test_get_config_lazily_loads_and_caches` _function_
- `test_substrate_config_flags_default_to_true` _function_
- `test_substrate_yaml_both_flags_disable_independently` _function_
- `test_substrate_yaml_missing_section_preserves_defaults` _function_
- `test_substrate_yaml_single_flag_leaves_other_default` _function_
- `test_substrate_yaml_unknown_keys_ignored` _function_

### `tests.test_config_store`

- `test_protocol_satisfaction_and_empty_store_safety` _function_
- `test_record_round_trip_overwrite_and_delete` _function_

### `tests.test_diagnostics_store`

- `test_age_based_retention_leaves_newer_rows` _function_
- `test_handler_stamps_contextvars_identity` _function_
- `test_normalize_stream_line_collapses_cr_frames` _function_
- `test_retention_on_missing_db_is_noop` _function_
- `test_size_budget_retention_deletes_oldest_first` _function_
- `test_store_append_and_exact_job_correlation` _function_

### `tests.test_empirical_store`

- `test_compute_config_hash_canonicalization` _function_
- `test_delete_record_and_empty_db_safety` _function_
- `test_multi_instance_and_multi_config_keying` _function_
- `test_sg54_api_usage_totals_accumulate` _function_
- `test_sg54_pre_migration_db_upgrades_in_place` _function_
- `test_welford_mean_max_of_peaks_and_success_rate` _function_

### `tests.test_errors`

- `test_classify_exception_defaults` _function_
- `test_config_error_reparented_with_canonical_fields_invalid` _function_
- `test_map_bare_exception_to_job_error_structured_data_and_policy` _function_
- `test_mro_discipline_no_capability_error_extends_valueerror` _function_
- `test_substrate_typed_exceptions_anchor_under_the_right_category` _function_
- `test_worker_oom_error_is_the_track_a_resource_signal` _function_

### `tests.test_hashing`

- `test_hash_bytes_custom_algorithm` _function_
- `test_hash_bytes_format_and_determinism` _function_
- `test_hash_dict_canonical_insertion_order_independence` _function_
- `test_hash_dict_canonical_none_and_nesting` _function_
- `test_hash_file_streams_and_matches_hash_bytes` _function_
- `test_verify_hash_roundtrip_and_tamper_detection` _function_

### `tests.test_journal_store`

- `test_append_is_loud_on_storage_failure` _function_
- `test_append_query_roundtrip_and_cursor` _function_
- `test_liveness_routing_constants` _function_
- `test_query_missing_db_and_count_zero` _function_
- `test_terminal_state_events_history_query` _function_
- `test_unknown_event_types_roundtrip_untouched` _function_

### `tests.test_manager`

- `test_cr10_execute_routes_by_instance_id` _function_ — execute / enable / disable route to the correct CapabilityInstance.
- `test_cr10_generate_instance_id` _function_ — _generate_instance_id produces unique `{name}-{6-char-hex}` IDs.
- `test_cr10_instance_queries` _function_ — get_instance + list_instances filter correctly.
- `test_cr10_unload_canonical_keeps_remaining_instances` _function_ — Unloading the default instance with remaining multi-instances clears
- `test_cr10_validate_instance_id` _function_ — _validate_instance_id rejects malformed input.
- `test_cr10b_async_wrappers_forward_to_sync` _function_ — load_capability_async / unload_capability_async run the sync versions
- `test_cr10b_load_capabilities_concurrent` _function_ — load_capabilities_concurrent fans out specs and returns instance_id dict.
- `test_cr10b_max_concurrency_caps_in_flight` _function_ — max_concurrency caps simultaneous loads (semaphore enforces the cap).
- `test_cr10b_partial_failure_semantics` _function_ — fail_fast=False collects exceptions; fail_fast=True re-raises.
- `test_cr10b_unload_capabilities_concurrent` _function_ — unload_capabilities_concurrent fans out and returns success/failure dict.
- `test_cr12_worker_env_overlay_and_secret_actuation` _function_ — Overlay composition: unset secret OMITTED (not injected empty); visible
- `test_cr2_persistence_hooks_deferred_disable` _function_ — CapabilityConfigStore persistence + enable/disable hooks + deferred-on-disable
- `test_cr3_global_stats_async_typed_paths` _function_
- `test_cr3_global_stats_typed_paths` _function_
- `test_cr7_eviction_candidates_multi_axis` _function_ — _evict_for_resources no longer filters by requires_gpu (CR-7 multi-axis):
- `test_cr7_max_retries_exhausted_raises` _function_ — All retries fail → final CapabilityResourceError raised.
- `test_cr7_no_retry_when_max_retries_zero` _function_ — max_retries=0 → no retry, first failure propagates without reload.
- `test_cr7_no_sample_recording_when_store_absent` _function_ — Sample recording skipped silently when empirical_store is None.
- `test_cr7_sample_recording_persists` _function_ — Sample recording succeeds + persists; second execute folds via Welford.
- `test_cr7_track_a_workeroom_reloads` _function_
- `test_cr7_track_b_capability_resource_error_reloads` _function_
- `test_gpu_subtree_attribution_sums_grandchildren` _function_ — With a sysmon configured and a worker reporting subtree_pids, the sample's
- `test_gpu_subtree_attribution_without_sysmon_records_zero` _function_ — No sysmon configured → gpu_memory_mb_peak records as 0.0 (honest signal).
- `test_phase5a_platform_query_filters` _function_ — get_compatible_for_current_platform filters a synthetic discovered set;
- `test_sg17_bind_returns_binding_with_defensive_copy` _function_
- `test_sg17_binding_forwards_to_manager` _function_ — CapabilityBinding forwards to a stub manager with capability_name
- `test_sg33_concurrent_limiter_created_and_cached` _function_ — SG-33: per-instance asyncio.Semaphore created with the right cap + cached.
- `test_sg5_validate_config_against_schema` _function_ — Strict mode rejects unknown keys; lenient mode filters + logs.

### `tests.test_manifest_format`

- `test_config_schema_hash_canonical_deterministic_sensitive` _function_
- `test_manifest_to_dict_omits_unpopulated_optionals` _function_
- `test_missing_format_version_legacy_flat_raises` _function_
- `test_non_object_json_raises` _function_
- `test_pre_surface_manifest_parses_to_none` _function_
- `test_structural_surface_roundtrip_and_hash_determinism` _function_
- `test_unrecognized_format_version_raises` _function_
- `test_v2_roundtrip_fully_populated` _function_

### `tests.test_metadata`

- `test_capability_instance_defaults_and_tz_aware_created_at` _function_
- `test_capability_instance_multi_instance_differentiation` _function_
- `test_capability_meta_construction_and_equality` _function_
- `test_phase_5a_resource_requirements_integration` _function_

### `tests.test_platform`

- `test_conda_env_exists_false_for_nonexistent` _function_
- `test_current_platform_string_shape` _function_
- `test_exactly_one_os_detected` _function_
- `test_get_conda_command_micromamba_local_prefix` _function_
- `test_get_conda_command_per_conda_type` _function_
- `test_get_micromamba_binary_path_resolution` _function_
- `test_get_python_in_env` _function_
- `test_micromamba_download_url_unknown_platform_raises` _function_
- `test_micromamba_urls_cover_all_platforms` _function_
- `test_popen_isolation_kwargs` _function_
- `test_run_shell_command_echo` _function_
- `test_terminate_process_kills_live_child` _function_

### `tests.test_ports`

- `test_best_effort_lands_completed_despite_failures` _function_
- `test_composition_model_shape` _function_
- `test_direct_member_cancel_lands_cancelled` _function_
- `test_empty_composition_immediately_terminal_completed` _function_
- `test_extract_output_field_attribute_and_loud_misses` _function_
- `test_extract_output_field_dict_key_and_whole_result` _function_
- `test_fail_fast_derivation_failed_plus_skipped` _function_
- `test_failure_skips_transitive_dependents_housekeeping_cancel_stays_failed` _function_
- `test_parallel_fan_in_both_ready_immediately` _function_
- `test_pipe_progression_execution_time_binding` _function_
- `test_record_result_requires_terminal_state` _function_
- `test_resolve_node_kwargs_replaces_markers_keeps_statics` _function_
- `test_user_cancel_intent_dominates_failures` _function_
- `test_validate_derives_edges_from_markers` _function_
- `test_validate_empty_composition_is_valid` _function_
- `test_validate_rejects_cycles_including_self_reference` _function_
- `test_validate_rejects_duplicate_ids` _function_
- `test_validate_rejects_unknown_refs` _function_

### `tests.test_proxy`

- `DeathStubProxy` _class_
- `FakeResponse` _class_
- `HarvestStubProxy` _class_
- `HeaderResponse` _class_
- `ListJournal` _class_
- `StubProcess` _class_
- `test_chunk_cancellation_special_case` _function_
- `test_chunk_fatal_category` _function_
- `test_chunk_resource_category_with_shortfall` _function_
- `test_chunk_resource_category_without_shortfall` _function_
- `test_chunk_transient_category` _function_
- `test_chunk_unknown_category_is_forensic_runtime_error` _function_
- `test_chunk_user_input_category` _function_
- `test_execute_paths_carry_track_a_check` _function_
- `test_exit_code_death_classifies_as_transient` _function_
- `test_harvest_absent_header_is_noop` _function_
- `test_harvest_envelope_less_rows_stay_unattributed` _function_
- `test_harvest_journals_accounts_with_envelope_identity` _function_
- `test_harvest_malformed_header_never_raises` _function_
- `test_sigkill_death_classifies_as_oom` _function_
- `test_sigsegv_death_classifies_as_transient` _function_
- `test_unary_fatal_job_error_raises_typed` _function_
- `test_unary_non_json_body_falls_back_to_runtime_error` _function_
- `test_unary_prefix_worker_falls_back_to_runtime_error` _function_
- `test_unary_resource_job_error_raises_typed` _function_
- `test_worker_alive_returns_silently` _function_
- `test_worker_already_cleaned_up_returns_silently` _function_

### `tests.test_queue`

- `AdmissionDeps` _class_ — Driver fake with the stage-3 admission surface.
- `FakeCancelCapability` _class_ — Worker proxy fake with cooperative cancel + slow execute.
- `FakeDeps` _class_ — Minimal concrete fake satisfying JobQueueDependencies.
- `FakeMeta` _class_
- `FakeSysmon` _class_ — CR-3 typed MonitorToolProtocol fake: get_system_status + list_processes.
- `FakeWorkerProxy` _class_ — Worker proxy fake: returns deterministic get_stats payload.
- `ProxySysmonDeps` _class_ — Driver fake supporting both worker-proxy + sysmon-capability lookups.
- `RunCorrelationDeps` _class_ — Driver fake that also captures the call envelope the queue set.
- `Stage4Deps` _class_ — Driver fake; exposes the _on_retry attribute slot used by start()/stop()
- `TaskChannelDeps` _class_
- `test_block_reason_events_dedupe` _function_
- `test_cancel_composition_before_dispatch` _function_
- `test_cancel_cooperative_success_phases` _function_
- `test_cancel_force_kill_phases` _function_
- `test_composition_pipe_binding` _function_
- `test_disabled_capability_gate_at_submit` _function_
- `test_empty_composition_completes_at_submit` _function_
- `test_event_bus_composition_tag_routing` _function_
- `test_event_bus_multi_subscriber_fanout` _function_
- `test_fail_fast_skips_dependents` _function_
- `test_gpu_ledger_co_runs_within_budget` _function_
- `test_gpu_ledger_serializes_over_budget` _function_
- `test_journal_primary_emission_and_durable_history` _function_
- `test_no_profile_job_runs_exclusive` _function_
- `test_parallel_nodes_co_run` _function_
- `test_per_instance_cap_defaults_to_one` _function_
- `test_per_instance_cap_opts_up` _function_
- `test_protocol_is_runtime_checkable` _function_
- `test_resource_snapshot_events_at_cadence` _function_
- `test_resource_snapshot_none_cases` _function_
- `test_resource_snapshot_with_sysmon` _function_
- `test_resource_snapshot_without_sysmon` _function_
- `test_retry_started_events` _function_
- `test_run_correlation_threading` _function_
- `test_sg13_eviction_signals_waiters` _function_
- `test_structural_validation_at_submit` _function_
- `test_subscriber_keys_routing` _function_
- `test_task_channel_routing` _function_
- `test_wedge_gate_refuses_new_submissions` _function_

### `tests.test_scheduling`

- `test_allocate_async_default_delegates_to_sync` _function_
- `test_permissive_scheduler_allows_everything` _function_

### `tests.test_secret_store`

- `test_protocol_and_empty_reads` _function_
- `test_round_trip_perms_and_no_value_leak` _function_
- `test_scope_folds_into_on_disk_shape_without_touching_default` _function_

### `tests.test_telemetry`

- `test_cpu_only_capability_returns_zero_not_none` _function_
- `test_dataclass_shaped_process_records_accepted` _function_
- `test_grandchild_gpu_usage_is_attributed` _function_
- `test_missing_subtree_pids_falls_back_to_worker_only` _function_
- `test_multi_pid_subtree_sums_and_takes_highest_vram_gpu_index` _function_
- `test_no_sysmon_returns_none` _function_
- `test_sysmon_errors_return_none` _function_

### `tests.test_validation`

- `ExampleConfig` _class_ — Example configuration dataclass with metadata constraints.
- `test_dataclass_to_jsonschema_structure_and_metadata` _function_
- `test_dict_to_config_validates_metadata_constraints` _function_
- `test_extract_defaults_covers_plain_and_factory_defaults` _function_
- `test_python_type_to_json_type_mapping` _function_
- `test_sg8_strict_rejects_unknown_keys_lenient_warns_and_filters` _function_
- `test_validate_config_and_config_to_dict` _function_

### `tests.test_wire`

- `MockAudioData` _class_ — Example class implementing FileBackedDTO.
- `test_accounts_accumulate_and_drain_once` _function_
- `test_accounts_header_ascii_json_roundtrip` _function_
- `test_duplicate_kind_guard` _function_
- `test_empty_or_absent_envelope_decodes_all_none` _function_
- `test_envelope_contextvar_pairing_and_executor_thread_propagation` _function_
- `test_envelope_roundtrip_drops_none_fields` _function_
- `test_envelope_tolerant_decode_ignores_unknown_keys` _function_
- `test_executor_thread_shares_capture_list` _function_
- `test_file_backed_dto_protocol_detection` _function_
- `test_flat_dto_roundtrips_typed` _function_
- `test_nested_dto_roundtrips_through_custom_from_dict` _function_
- `test_record_account_no_op_outside_capture_span` _function_
- `test_subclass_not_encoded_under_parents_kind` _function_
- `test_transport_terminus_tolerance` _function_
- `test_unknown_kind_passes_through_envelope_intact` _function_
- `test_unregistered_objects_pass_through_unchanged` _function_
- `test_wire_type_requires_dataclass` _function_

### `tests.test_worker`

- `AcctRecordingCapability` _class_ — execute records accounts (the T29 storage-helper shape).
- `AcctTaskAdapter` _class_ — Minimal task adapter: /task dispatch target for the TASK_ACCOUNT test.
- `CancellingCapability` _class_ — execute() raises CapabilityCancelledError to drive the 409 path.
- `CancellingStreamCapability` _class_ — execute_stream raises CapabilityCancelledError mid-stream — SG-52 typed
- `EnvelopeCapability` _class_ — Captures the contextvar AS SEEN INSIDE execute() (executor thread).
- `FailingCapability` _class_ — execute() raises a real RuntimeError — worker returns 500.
- `FailingStreamCapability` _class_ — execute_stream raises a bare RuntimeError — SG-52 typed error chunk with
- `NotAMonitorCapability` _class_ — NOT a monitor — lacks get_system_status / list_processes entirely.
- `PrefetchCapability` _class_ — Tracks prefetch + reconfigure invocations for endpoint verification.
- `PrefetchRaisingCapability` _class_ — prefetch() raises — worker returns 500 with detail.
- `RaisingMonitorCapability` _class_ — IS a monitor shape but raises NotImplementedError — a legacy monitor
- `StreamCapability` _class_ — execute_stream yields items normally; verifies SG-51's flag reset.
- `test_accounts_ride_response_header` _function_
- `test_call_envelope_reaches_executor_thread_and_never_leaks` _function_
- `test_enhanced_json_encoder_dataclass` _function_
- `test_enhanced_json_encoder_datetime` _function_
- `test_execute_cancellation_surfaces_as_409` _function_
- `test_execute_real_failure_stays_500_with_job_error` _function_
- `test_monitor_endpoints_404_when_not_a_monitor` _function_
- `test_monitor_endpoints_501_when_opted_out` _function_
- `test_prefetch_and_reconfigure` _function_
- `test_prefetch_raising_returns_500` _function_
- `test_stream_cancellation_emits_typed_terminal_chunk` _function_
- `test_stream_failure_emits_typed_terminal_chunk` _function_
- `test_stream_resets_cancel_flag_before_iteration` _function_

### `tests_manual.validate_stage7_observability_e2e`

- `main` _function_
- `part1_latency` _function_
- `part2_wedge` _function_
- `part3_kill9` _function_
- `part4_retention_race` _function_
- `part5_late_subscriber` _function_

## Dependencies

**Used by:** `cjm-context-graph-projection`, `cjm-markdown-decompose-core`
