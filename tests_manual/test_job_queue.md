# Tombstone — `test_job_queue.py` (RETIRED 2026-06-18, stage 9)

**Origin:** `cjm-plugin-system/tests_manual/test_job_queue.py` (2026-01-03, pre-overhaul).
**Retired because:** pre-overhaul JobQueue integration test; the queue gained the stage-3 composition ports + multi-lane dispatch since. Per the stage-9 decision the pre-overhaul `tests_manual` cohort is retired, not patched.

**What it validated:** JobQueue integration — (1) submitting multiple jobs, (2) monitoring queue state, (3) cancelling jobs, (4) priority ordering.

**Coverage status:** the happy-path queue is exercised end-to-end by the cores; the focused **cancel** + **priority-ordering** contract is not directly re-asserted by a core.

**Reimplementation target (first principles):** a `cjm-substrate` queue test over the current `submit(...)` + composition-ports surface — preserve the cancel + priority-ordering invariants.
