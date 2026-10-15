# cjm-substrate

<!-- generated from the context graph by `cjm-context-graph readme` — do not edit by hand; edit the graph (the urge to hand-edit = move it on-graph) -->

A dependency-isolated capability-composition runtime: heterogeneous tools run in their own environments behind a uniform HTTP/JSON boundary; the host composes them into workflows with resource-aware scheduling and threads provenance through a context graph.

## Modules

- **`cjm_substrate.__init__`**
- **`cjm_substrate.bootstrap`** — One-call factory that assembles a CapabilityManager + JobQueue + capability
- **`cjm_substrate.cli`** — CLI tool for declarative capability management.
- **`cjm_substrate.core.__init__`**
- **`cjm_substrate.core._telemetry`** — Shared GPU/CPU attribution helpers used by both JobQueue._sample_resource_snapshot (CR-6 Stage 3) and CapabilityManager._record_sample_safe (CR-7).
- **`cjm_substrate.core.adapter`** — The typed-task half of the capability-unit fracture (pass-2 Thread 3) —
- **`cjm_substrate.core.adapter_manifest`** — The ADAPTER unit's registration manifest + the surface-based compatibility matcher (CR-17 pt 2, stage 4). Pass-2 Thread 3: registration/discovery = per-unit manifests generated in-env and found by discover_manifests(); compatibility is DERIVED, not declared — the capability records only its structural surface, the adapter declares its protocol (recorded here as member names + parameter lists), and the substrate matches manifest-vs-manifest. Works against UNLOADED capabilities with zero protocol imports host-side.
- **`cjm_substrate.core.capability`** — The tool-capability interface — the manage-the-tool half of the capability-unit fracture (pass-2 Thread 3): identity, lifecycle, config, cancellation, and observability for a tool running in a worker process, serving both concrete capabilities (in workers) and remote proxies (in the host). The task channel is deliberately NOT here: typed task contracts live on task adapters (core.adapter + the per-task cjm-<task>-adapter-interface libraries) and results cross the worker boundary through the typed wire layer (core.wire); execute_stream's transitional default remains for fused-era capabilities only. Also home to the CR-12 worker-env contract (EnvVarSpec + the Q1-A template vocabulary), the SG-44/T28 action-dispatcher convention, and the pass-2 Thread-3 structural-surface derivation.
- **`cjm_substrate.core.config`** — Project-level configuration for paths, runtime settings, and environment management.
- **`cjm_substrate.core.config_store`** — Persistent storage for per-instance capability configuration (config + enabled flag + worker-env override), keyed by instance_id.
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
- **`cjm_substrate.core.workspace`** — Workspace resolution: the marker-rooted directory that owns a pipeline's local artifacts (runs, graph data, substrate stores, TUI sidecars).
- **`cjm_substrate.utils.__init__`**
- **`cjm_substrate.utils.cache_paths`** — Per-(input-content, config) deterministic cache directories for capability outputs. Same (input content, action, config) always resolves to the same directory; any change to input content OR config produces a different one — no silent overwrites, no stale-artifact accumulation, and chained invalidation for capability sequences (see the cache-paths-design-provenance note for the ffmpeg-bug origin story).
- **`cjm_substrate.utils.envrefresh`** — The worker-env REFRESH verb (DEC f282571c, work item dbbaa8b4): every worker
- **`cjm_substrate.utils.envtruth`** — Env truth for capability-served libs — the ONE sweep the six sightings demanded
- **`cjm_substrate.utils.hashing`** — Shared cryptographic hashing primitives for content integrity verification.
- **`cjm_substrate.utils.lifecycle`** — Artifact lifecycle sidecar — the ONE seam every picker filters through
- **`cjm_substrate.utils.sidecar`** — JSON sidecar for shell view-state — settings and bookmarks that persist
- **`cjm_substrate.utils.validation`** — Validation helpers for capability configuration dataclasses.
- **`scripts.archive_canonicalize`** — The archive's one canonicalization commit (design 56c9c332 (6); work item f86be52f).
- **`scripts.author_edits`** — Run journaled `cg-write author --edit` ops from a spec file (argv lists, no shell quoting).
- **`scripts.cascade_manifests`** — cascade_manifests.py
- **`scripts.claude_git_rules`** — The git allow rules of .claude/settings.local.json, generated from the window's ONE inventory.
- **`scripts.code_absorb_replay`** — The element-grain absorb replayed over the code source journal's real edit history, in memory --
- **`scripts.code_canonicalize`** — The code lane's one canonicalization commit (design abfc2829 (4), refined by d26e00f9).
- **`scripts.code_capture_preview`** — The element capture's acceptance preview, in memory -- nothing is journaled (build b3339c8f; design
- **`scripts.code_lane_census`** — Census of the code lane's source journal for the element source unit (design abfc2829;
- **`scripts.cold_copy`** — cold_copy.py
- **`scripts.evidence_pdf_check`** — Check Cloudflare's dashboard exports against the API snapshot (design 7f315830; the DoD of fbf7fc0e:
- **`scripts.evidence_reconcile`** — Reconcile a Cloudflare evidence snapshot's derived months against Cloudflare's own figures
- **`scripts.flywheel_backup`** — flywheel_backup.py
- **`scripts.model_cache_inventory`** — model_cache_inventory.py
- **`scripts.pypi_pin_bridge`** — Pin-bridge matrix over the window's unpublished repos (the sweep's inventory): every
- **`scripts.pypi_sweep`** — Local-vs-PyPI version sweep over every cjm-* repo with a pyproject — the release-rung
- **`scripts.site_about_page`** — Render proof of design ff0c6338 and amendments 2364f215 / 2fba772c (builds df6b8ae6 and
- **`scripts.site_category_descriptions`** — Render proof of amendments e38d403c (4) and 397f8fa3: every earned category page states its
- **`scripts.site_chip_links`** — Render proof of design a62f2499 (7)-(8): every chip on every rendered page (listing chips and
- **`scripts.site_feed_compare`** — Every feed of a rendered site against a baseline's, item for item -- the render proof of the
- **`scripts.site_graph_chips`** — Every public post's and projected page's chips from the notes graph -- the chips file the
- **`scripts.site_home_page`** — Render proof of designs e55201e2 and 8b4f15d0 (amendment 5c3c2662, erratum 2e2abe66; builds
- **`scripts.site_listing_items`** — Every listing page's items, in rendered order, from a rendered site — the listing-level proof
- **`scripts.site_main_content_diff`** — Before/after MAIN-CONTENT diff of every rendered post page between two site renders.
- **`scripts.site_review_server`** — Serve a built site for a render review: the tree as is, plus /__mode?to=<path>&m=dark|light, which

## API

### `cjm_substrate.bootstrap`

- `Pipeline` _class_ — Assembled substrate stack: manager + queue + capability bindings.
- `create_pipeline` _function_ — Assemble a CapabilityManager + JobQueue + capability bindings in one call.

### `cjm_substrate.cli`

- `derive_public_capabilities_cmd` _function_ — Project the dev capabilities yaml to its PUBLIC form (ruling 8299fb9d, DEC f282571c):
- `envs_for_cmd` _function_ — Env-truth sweep (work item 424b9781; MODE-AWARE per DEC f282571c): every env the
- `generate_adapter_manifest` _function_ — CR-17 pt 2 (stage 4): introspect a task-adapter impl in-env and write its adapter manifest.
- `install_all` _function_ — Install and register all capabilities defined in capabilities.yaml.
- `list_capabilities` _function_ — List installed capabilities from manifest directory.
- `list_secrets` _function_ — List the secret KEY NAMES stored for a capability — never the values (CR-12).
- `logs_command` _function_ — Tail / follow the observability stores (CR-14).
- `main` _function_ — cjm-substrate CLI for managing isolated capability environments.
- `refresh_cmd` _function_ — Refresh every worker env from the closure it was installed from (DEC f282571c, work
- `regenerate_manifest` _function_ — Re-run introspection for an installed capability and rewrite its manifest.
- `remove_capability` _function_ — Remove a capability's manifest and conda environment.
- `retention_command` _function_ — Apply the diagnostics retention policy now (CR-14).
- `run_cmd` _function_ — Run a shell command and stream output.
- `set_secret` _function_ — Store a capability secret in the project-local SecretStore (CR-12).
- `setup_host` _function_ — Install interface libraries in the current Python environment.
- `setup_runtime` _function_ — Download and setup micromamba runtime for project-local mode.
- `validate_file` _function_ — SG-6 + T23: validate a manifest / capabilities.yaml / capability source.
- `workspace_doctor_cmd` _function_ — Run workspace integrity checks; exits non-zero when any check warns.
- `workspace_init` _function_ — Declare a workspace: write the marker + create the conventional layout (idempotent; an existing marker is left untouched).

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
- `load_config` _function_ — Load config with layered resolution (CLI > env vars > yaml > workspace > defaults).
- `reset_config` _function_ — Reset to unloaded state (for testing).
- `set_config` _function_ — Set current config (called by CLI callback).

### `cjm_substrate.core.config_store`

- `CapabilityConfigRecord` _class_ — Persisted state for a capability INSTANCE: config + enabled flag + worker-env override.
- `CapabilityConfigStore` _class_ — Protocol for persisting per-instance `CapabilityConfigRecord` across sessions.
- `LocalCapabilityConfigStore` _class_ — SQLite-backed default implementation of `CapabilityConfigStore`.
- `delete` _function_ — Remove the record for an instance.
- `get` _function_ — Fetch the record for an instance.
- `list_all` _function_ — Return all stored records keyed by instance_id.
- `set` _function_ — Persist a record. Stamps `updated_at` to the current time.

### `cjm_substrate.core.diagnostics_store`

- `DiagnosticRecord` _class_ — One structured worker log record (CR-14 diagnostics class).
- `DiagnosticsLogHandler` _class_ — Worker-side logging handler writing `DiagnosticRecord`s (CR-14).
- `DiagnosticsStore` _class_ — Protocol for the disposable diagnostic-narrative store (CR-14).
- `LocalDiagnosticsStore` _class_ — SQLite-backed default `DiagnosticsStore` (CR-14).
- `StreamChunk` _class_ — One raw stdout/stderr line the host pump captured (death-rattle floor).
- `append_chunk` _function_ — Persist one raw stream line.
- `append_record` _function_ — Persist one structured record.
- `append_records` _function_ — Persist several records in ONE transaction (ruling b7b3976f (A)(2): a worker's records
- `apply_retention` _function_ — Retention as a QUERY (the CR-14 reframe's mechanical payoff).
- `flush_worker_diagnostics` _function_ — Commit the worker's buffered diagnostics records (ruling b7b3976f (A)(2)): the worker calls
- `install_worker_diagnostics` _function_ — Configure worker-process logging (replaces the old `basicConfig`).
- `maybe_sweep` _function_ — The store's ONE maintenance step (ruling b7b3976f (A)(1) / (3)): at most once per interval
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
- `substrate_mismatch` _function_ — DEC f282571c: compare the substrate the worker RUNS (its /health

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
- `call_metrics` _function_ — The current call's measurement dict, or None outside a queued job's call.
- `drain_accounts` _function_ — Return + clear the current span's recorded accounts ([] outside a span
- `flat_from_dict` _function_ — Default reconstruction for FLAT wire DTOs (no nested-DTO fields).
- `get_call_envelope` _function_ — The current call envelope, or None outside any call span.
- `record_account` _function_ — Record one substrate-family account for the current call span.
- `reset_call_envelope` _function_ — Restore the prior envelope (always pair with `set_call_envelope` in finally).
- `reset_call_metrics` _function_ — Restore the prior measurement dict (always pair with `set_call_metrics` in finally).
- `set_call_envelope` _function_ — Set the current call envelope; returns the token for `reset_call_envelope`.
- `set_call_metrics` _function_ — Set the current call's measurement dict; returns the token for `reset_call_metrics`.
- `wire_decode` _function_ — Reconstruct a typed result from its tagged envelope (host side).
- `wire_encode` _function_ — Wrap a registered wire DTO in its tagged envelope (worker side).
- `wire_type` _function_ — Register a dataclass as a typed wire DTO under `kind`.

### `cjm_substrate.core.worker`

- `EnhancedJSONEncoder` _class_ — JSON encoder that handles dataclasses and other common types.
- `create_app` _function_ — Create FastAPI app that hosts the specified capability.
- `parent_monitor` _function_ — Monitor parent process and terminate self if parent dies.
- `run_worker` _function_ — CLI entry point for running the worker.

### `cjm_substrate.core.workspace`

- `Workspace` _class_ — A resolved workspace: the marker-rooted directory owning pipeline artifacts.
- `WorkspaceError` _class_ — A workspace was named (flag or env) but could not be resolved.
- `find_workspace_root` _function_ — Upward walk — the git-style discovery that makes workspace identity launch-cwd-independent.
- `init_workspace` _function_ — Declare a workspace: write the marker + create the conventional layout.
- `relativize_recorded` _function_ — Writer half of the 5daadfc4 recording contract (rung f).
- `resolve_recorded_tree` _function_ — Reader half of the recording contract (rung f; anchor rule ratified 2026-07-19).
- `resolve_workspace` _function_ — Resolve the active workspace: explicit flag > CJM_WORKSPACE env > upward walk > None.
- `workspace_doctor` _function_ — Integrity-check skeleton for the workspace doctor verb.

### `cjm_substrate.utils.cache_paths`

- `cache_dir_for_config` _function_ — Return (and optionally create) a per-(input-content, config) cache directory.
- `list_cache_entries` _function_ — Enumerate all per-config cache directories for a given (input, action).
- `prune_cache_for_input` _function_ — Delete per-config cache directories for `(input, action)`, optionally

### `cjm_substrate.utils.envrefresh`

- `EnvPlan` _class_ — One env's refresh plan — every command the verb will run, in order, plus
- `checkout_dist_version` _function_ — (dist name, version) from a checkout's pyproject.toml — the name from
- `checkout_remote` _function_ — (origin url, default branch) of a checkout — `git config` for the url,
- `checkout_root` _function_ — The checkout that owns `path`: the nearest ancestor (or `path` itself)
- `default_checkouts_root` _function_ — Dev mode's checkouts root when none is given — env truth's
- `derive_public_capabilities` _function_ — The public yaml: every path-valued `package` / `interface_libs[]` /
- `execute_plan` _function_ — Run the plan's commands in order; a failed install stops the env (a
- `find_checkout` _function_ — The checkout a dist refreshes from in dev mode: its editable origin when
- `import_sweep` _function_ — Rung 3 of the window-close ritual as a verb: from a NEUTRAL cwd (a temp
- `plan_env` _function_ — The pure planning step: which pip commands refresh this env, in order.
- `public_spec` _function_ — A dev path spec -> `<dist>>=<version>` (the version the checkout
- `pypi_release_exists` _function_ — The register's first-publish gate: read the PER-RELEASE endpoint (the
- `raw_github_url` _function_ — A GitHub origin (ssh or https, with or without .git) -> the raw file URL
- `render_public_yaml` _function_ — The yaml text with its provenance header — a projection (regenerate it
- `run_argv` _function_ — The default runner: stream when the operator should watch (pip installs),
- `running_workers` _function_ — Every live `cjm_substrate.core.worker` process whose interpreter lives in
- `spec_floors` _function_ — Every (dist, floor version) a public yaml pins with `>=`/`==` — the set
- `yaml_entry_for` _function_ — The capabilities-yaml entry that built this env — matched on `env_name`
- `yaml_specs` _function_ — Every pip spec a yaml entry installs into its env: interface libs, adapter

### `cjm_substrate.utils.envtruth`

- `checkouts_root` _function_ — Dev mode's checkouts root: the parent of the host substrate's own editable
- `derive_mode` _function_ — Mode from a package spec (DEC f282571c): a filesystem path or an `-e `
- `env_mode` _function_ — The env's mode: the manifest's recorded `install.mode` when it is one of
- `envs_for` _function_ — Every env the manifests say serves `lib` — or whose interpreter would
- `host_substrate` _function_ — The host side of the launch-time check (DEC f282571c): the substrate this
- `main` _function_
- `read_cjm_set` _function_ — The WHOLE cjm-* set the interpreter at `python_path` would import — the
- `render_rows` _function_ — The human sweep report + the post-edit checklist verdict line.
- `source_version` _function_ — The version an editable install really runs: a static `project.version`

### `cjm_substrate.utils.hashing`

- `hash_bytes` _function_ — Compute a hash of byte content.
- `hash_dict_canonical` _function_ — Hash a dict via canonical JSON encoding.
- `hash_file` _function_ — Stream-hash a file without loading it entirely into memory.
- `verify_hash` _function_ — Verify content against an expected hash string.

### `cjm_substrate.utils.lifecycle`

- `ArtifactLifecycle` _class_ — One artifact directory's lifecycle sidecar: forgiving reads (absent /
- `LifecycleRefusal` _class_ — A lifecycle verb refused LOUDLY (not an artifact dir, an unknown
- `artifact_id` _function_ — Every artifact class names its directory by the id its manifest
- `build_parser` _function_
- `default_actor` _function_ — CJM_ACTOR when the environment carries one (the wrappers stamp
- `find_holders` _function_ — Every manifest/marker under the workspace that names the artifact —
- `lifecycle_state` _function_ — The index-row question: given a row's manifest path, its state.
- `list_artifacts` _function_ — Every artifact directory under a class dir (a dir with a manifest)
- `main` _function_
- `partition_lifecycle` _function_ — Stamp + split: the active side is what a picker lists by default,
- `stamp_lifecycle` _function_ — Stamp each row's `_lifecycle` in place (rows without a path read active).
- `workspace_root_for` _function_

### `cjm_substrate.utils.sidecar`

- `SidecarState` _class_ — One JSON sidecar file: never-raise load, merge-on-save, best-effort write.

### `cjm_substrate.utils.validation`

- `config_to_dict` _function_ — Convert a configuration dataclass instance to a dictionary.
- `dataclass_to_jsonschema` _function_ — Convert a dataclass to a JSON schema for form generation.
- `dict_to_config` _function_ — Create a configuration dataclass instance from a dictionary.
- `extract_defaults` _function_ — Extract default values from a configuration dataclass type.
- `validate_config` _function_ — Validate all fields in a configuration dataclass against their metadata constraints.
- `validate_field_value` _function_ — Validate a value against field metadata constraints.

### `scripts.archive_canonicalize`

- `canonical_posts` _function_ — Each kept post at HEAD beside its composed form, with the heading changes classified.
- `main` _function_

### `scripts.author_edits`

- `main` _function_
- `parse` _function_

### `scripts.cascade_manifests`

- `ProjectScope` _class_ — One substrate-installation scope: a cjm.yaml + the manifests_dir it resolves to.
- `classify_manifest` _function_ — Return (status, reason) for a single manifest file.
- `discover_ecosystem` _function_ — Ecosystem mode: walk `base_path/cjm-*/` and collect per-project scopes.
- `discover_single_project` _function_ — Single-project mode: resolve one ProjectScope from cwd or override.
- `format_only_upgrade` _function_ — Load via load_manifest (handles v1.0 shim) and re-write as v2.0.
- `main` _function_
- `run_regenerate` _function_ — Invoke `cjm-ctl regenerate-manifest <capability_name>` as a subprocess.

### `scripts.claude_git_rules`

- `generated_rules` _function_ — One rule per repo per subcommand, the checkout's path spelled out literally.
- `is_replaced` _function_ — Every `git -C` rule: a wildcarded one (the startup warning) or an exact one this
- `main` _function_
- `plan` _function_ — The new allow list: the old one less every `git -C` rule, the generated rules where the

### `scripts.code_absorb_replay`

- `main` _function_
- `replay` _function_

### `scripts.code_canonicalize`

- `canonical_text` _function_ — The module composed from its elements, its blank lines empty.
- `main` _function_
- `plan` _function_

### `scripts.code_capture_preview`

- `capture_groups` _function_ — A capture group for every live, cut-over test module, as of the journal's end (ts after its last record).
- `fold` _function_
- `main` _function_

### `scripts.code_lane_census`

- `amplification` _function_ — Full-state bytes journaled against the lines each record changed.
- `class_seams` _function_ — Blank-line seams inside top-level classes (a member's leading comments belong to it).
- `corpus_profile` _function_ — Modules, symbols, text-region placement and import-time order dependencies.
- `main` _function_
- `test_import_blocks` _function_ — Whether a test module's rendered bindings reproduce its verbatim import block (f4fb397f;

### `scripts.cold_copy`

- `cache_entry_path` _function_ — Where an inventory entry lives in the cache (its whole hub repo dir, or the file).
- `copy_and_verify` _function_ — Copy, then hash source and copy; (verified, source tree hash, files, bytes).
- `copy_tree` _function_ — rsync the source to the destination, symlinks as symlinks (`-a`); a directory's
- `ledger_has` _function_ — The ledger's record of this entry on this volume at this tree hash, if any.
- `load_ledger` _function_ — The cold ledger ({"copies": [...]}); absent = empty, unreadable raises.
- `main` _function_ — CLI: copy the named cache entries (+ the flywheel's artifacts) cold, verify, record.
- `tree_hash` _function_ — (`sha256:<hex>`, file count, bytes) over a tree: each regular file as its relative
- `volume_identity` _function_ — (label, uuid) of the filesystem holding the cold root (`findmnt -T`) — a copy is
- `write_ledger` _function_ — The ledger beside the inventory AND on the volume (the drive names its contents).

### `scripts.evidence_pdf_check`

- `main` _function_
- `read_pdf` _function_ — The window (UTC start, UTC end) and the top paths [(path, visits)] a dashboard export states.

### `scripts.evidence_reconcile`

- `main` _function_

### `scripts.flywheel_backup`

- `already_backed_up` _function_ — The sidecar's backup record for this repo at the artifact's CURRENT content hash,
- `discover_artifacts` _function_ — Every flywheel artifact dir whose manifest carries a known format: its id, class,
- `main` _function_ — CLI: discover, push (skipping what the sidecars already record), verify, record.
- `push_artifact` _function_ — Upload the artifact dir under <id>/ in the repo as ONE commit; returns the commit
- `verify_artifact` _function_ — A FRESH download of <id>/ at the pushed revision into a temp dir (its own cache —

### `scripts.model_cache_inventory`

- `annotate_working_set` _function_ — Fold the keep pins (`kept` = the user's reason) and the cold ledger's copies onto the
- `build_manifest` _function_ — The whole manifest: every cache entry across the stores, the flywheel's local-only
- `entry_key` _function_ — `<source>:<repo_id>` — the one handle the keep file, the cold ledger and the
- `entry_needles` _function_ — The strings whose presence in a tracked file means the code NAMES this entry: a hub
- `flat_entries` _function_ — The flat checkpoint stores: whisper `<name>.pt` (re-pull by name through the whisper
- `flywheel_entries` _function_ — The LOCAL-ONLY class the cache walk cannot see: the data flywheel's training runs
- `hub_entries` _function_ — One entry per Hugging Face hub repo cache dir (`models--org--name` / `datasets--…`):
- `load_json_file` _function_ — A JSON side file (the keep file, the cold ledger), or `default` when it does not
- `main` _function_ — CLI: inventory the cache root (+ the flywheel workspace, + the working set) into the
- `ollama_entries` _function_ — One entry per Ollama model manifest (`manifests/<registry>/<ns>/<model>/<tag>`); the
- `prune` _function_ — List (and with `yes`, delete) everything outside the working set. The Ollama blobs
- `prune_targets` _function_ — What a prune removes, path by path: each prunable entry's on-disk path (a hub repo
- `render_markdown` _function_ — The manifest as a size-sorted table (largest first) with the totals and caveats;
- `render_repull` _function_ — A shell script that re-pulls every entry at its recorded revision (HF_HOME must point
- `scan_references` _function_ — Annotate each entry with `referenced_by`: every `<repo>/<path>` under the code root

### `scripts.pypi_pin_bridge`

- `cjm_pins` _function_ — (dep, op, floor) for every cjm-* dependency the repo's pyproject declares.
- `main` _function_

### `scripts.pypi_sweep`

- `archived_repos` _function_ — Names of the owner's GitHub repos that are archived (one gh call; empty if gh is absent).
- `local_version` _function_ — A repo's version: the static pyproject `version`, else the package's `__version__`.
- `main` _function_
- `pypi_releases` _function_ — The distribution's releases on PyPI, sorted (None = 404, never published). Cached.
- `sweep` _function_ — One row per non-archived cjm-* repo with a pyproject: repo, name, version, latest, state.
- `uploaded` _function_ — The ISO date PyPI first received `version` (None = the distribution's FIRST release), or
- `vkey` _function_

### `scripts.site_about_page`

- `norm` _function_
- `page_of` _function_ — The rendered file an About link opens (a directory -> its index.html).
- `plain` _function_ — A markdown paragraph as its rendered text: link targets and emphasis markers dropped.

### `scripts.site_category_descriptions`

- `read` _function_
- `vocab_of` _function_

### `scripts.site_feed_compare`

- `compare` _function_
- `normalize` _function_
- `parse_feed` _function_

### `scripts.site_graph_chips`

- `chips_map` _function_

### `scripts.site_home_page`

- `hubs_of` _function_ — The distinct pages of one shape a rendered index page links from its main content.
- `listed` _function_ — How many listing items a rendered page lists (Quarto renders every item; pagination is client-side).
- `norm` _function_
- `page_of` _function_ — The rendered file a link opens from the page at `base` (a directory -> its index.html).
- `pattern` _function_ — The path shape a hub page shares with its siblings: its varying segment as a wildcard.
- `soup_of` _function_
- `stated` _function_ — What a rendered page states: its title (the <title> before the site suffix), its meta

### `scripts.site_listing_items`

- `capture` _function_
- `compare` _function_

### `scripts.site_main_content_diff`

- `blocks` _function_ — The page's top-level leaf blocks as normalized text (None when the page has no <main>).
- `kind` _function_ — A known derived-block kind, or '' (unexplained).
- `main` _function_

### `scripts.site_review_server`

- `H` _class_

## Dependencies

**Depends on:** `fastapi`, `fastcore`, `httpx`, `psutil`, `pyyaml`, `typer`, `uvicorn`
**Used by:** `cjm-capability-demucs`, `cjm-capability-ffmpeg`, `cjm-capability-graph-sqlite`, `cjm-capability-monitor-nvidia`, `cjm-capability-primitives`, `cjm-capability-pyannote`, `cjm-capability-pysbd`, `cjm-capability-qwen3-forced-aligner`, `cjm-capability-silero-vad`, `cjm-capability-voxtral-hf`, `cjm-capability-whisper`, `cjm-context-graph-layer`, `cjm-context-graph-primitives`, `cjm-context-graph-projection`, `cjm-graph-storage-adapter-interface`, `cjm-markdown-decompose-core`, `cjm-sentence-segmentation-adapter-interface`, `cjm-speaker-diarization-adapter-interface`, `cjm-substrate-hf-utils`, `cjm-substrate-torch-utils`, `cjm-transcript-correction-core`, `cjm-transcript-correction-qt`, `cjm-transcript-decomp-core`, `cjm-transcript-decomp-qt`, `cjm-transcription-adapter-interface`, `cjm-transcription-core`, `cjm-transcription-qt`, `cjm-vad-adapter-interface`, `cjm-workflow-hub-qt`
