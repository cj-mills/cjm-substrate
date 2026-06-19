# Tombstone — `test_scheduling.py` (RETIRED 2026-06-18, stage 9)

**Origin:** `cjm-substrate/tests_manual/test_scheduling.py` (2025-12-25, pre-overhaul).
**Retired because:** tests the **pre-stage-3** `SafetyScheduler`/`QueueScheduler` admission model with a `MockMonitorPlugin`; superseded by stage-3 **resource-DERIVED** admission (per-instance cap + empirical GPU/RAM peaks keyed by config-hash + live sysmon headroom). Per the stage-9 decision the pre-overhaul cohort is retired, not patched.

**What it validated:** admission gating on VRAM headroom — a heavy plugin needing 4 GB is blocked when the (mock) monitor reports only 2 GB free; the scheduler admits only within measured headroom.

**Coverage status:** the *behavior* (headroom-gated admission) survives in stage-3 form; the cores' `measure_cold_*_overlap_e2e` tests exercise real multi-lane admission. The mock-monitor unit shape is obsolete.

**Reimplementation target (first principles):** if a focused admission unit test is wanted, write it against the stage-3 admission surface (`get_admission_profile` / empirical store / live headroom) in `cjm-substrate` — preserve the "never admit beyond measured headroom" invariant.
