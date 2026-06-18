# Tombstone — `test_federation.py` (RETIRED 2026-06-18, stage 9)

**Origin:** `cjm-plugin-system/tests_manual/test_federation.py` (2026-01-04, pre-overhaul). Sibling of the also-retired `test_graph_federation.py` (see `test_graph_federation.md`).
**Retired because:** pre-overhaul "Model Arena" demo on the early JobQueue + raw cross-DB DuckDB joins; predates the CR-18 graph layer + the typed query expression. Per the stage-9 decision the pre-overhaul cohort is retired, not patched.

**What it validated:** (1) JobQueue managing transcription jobs, (2) sequential execution respecting GPU resources, (3) queue-state visibility during execution, (4) **data federation via DuckDB across plugin databases**.

**Coverage status:** the GPU-respecting sequential execution + queue visibility are now stage-3 admission (cores cover them); the **cross-store federation** intent overlaps `test_graph_federation` and has no current-era home.

**Reimplementation target (first principles):** reimplement federation against the CR-18 graph layer + typed `NodeQuery`/`EdgeQuery`/`RawQuery` (cross-source correlation by content-hash `SourceRef`), NOT raw DuckDB joins — same note as `test_graph_federation.md`. The Nanjing I↔II cross-reference is the real exemplar.
