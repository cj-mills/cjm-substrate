# Tombstone — `test_zombie_pact.py` (RETIRED 2026-06-18, stage 9)

**Origin:** `cjm-substrate/tests_manual/test_zombie_pact.py` (2025-12-26, pre-overhaul).
**Retired because:** pre-overhaul process-lifecycle test built on the early `PluginManager.load_plugin`/`get_plugin` surface. Per the stage-9 decision the pre-overhaul `tests_manual` cohort is retired, not patched.

**What it validated (worker-subprocess orphan/zombie reaping):** a "victim" process loads a plugin (spawning a worker subprocess), prints `WORKER_PID`, then the test runner kills the victim — verifying the worker subprocess is **reaped, not left a zombie/orphan** when its parent dies.

**Coverage status:** UNIQUE — the cores' happy-path runs don't exercise abrupt parent death / orphan reaping. Relates to the CR-3 subtree-attribution + `terminate_process`/subtree-kill machinery.

**Reimplementation target (first principles):** a `cjm-substrate` process-lifecycle test asserting worker subtree cleanup on parent termination — the durable invariant is "no orphaned worker survives a killed host."
