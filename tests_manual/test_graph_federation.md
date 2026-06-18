# Tombstone — `test_graph_federation.py` (RETIRED 2026-06-18, stage 9)

**Origin:** `cjm-plugin-system/tests_manual/test_graph_federation.py` (vintage **January 2026**, pre-overhaul).
**Retired because:** imported `SourceRef` + `context_to_mermaid` from the now-dissolved `cjm-graph-plugin-system` (GitHub-archived 2026-06-18); also built on `execute(action=…)` dispatch and carried lean stand-ins for the stage-5-dissolved `cjm-graph-domains` knowledge sketch. The whole shape predates the Option C task channel + the CR-18 graph layer. Per the stage-9 decision, the pre-overhaul `tests_manual` cohort is **retired, not patched** — coverage is redesigned from first principles against the cores/substrate, not carried forward.

**What it validated (the "ingestion" lifecycle demo, Art of War corpus):**
1. Transcribe audio (Voxtral) → Job ID.
2. Build type-safe domain nodes (`Person`/`Work`/`Concept`/`Topic`/`Quote` + `KnowledgeRelations` AUTHORED/DISCUSSES/MENTIONS) over the generic `GraphNode`.
3. Push nodes to the context graph via `execute(action=…)`.
4. **Federation query:** DuckDB JOIN across the transcript DB and the graph DB (cross-store correlation by `SourceRef` content hash).

**Coverage status:** UNIQUE intent (cross-source federation + cross-DB join + `context_to_mermaid` graph viz) — but the *implementation* is entirely pre-overhaul; **no core covers it.**

**Reimplementation target / notes (first principles, do NOT port):**
- Federation is the **Nanjing I↔II cross-reference** exercise made real — reimplement against the CR-18 graph layer + the typed query expression (decomp/correction cores or a new substrate test), not `execute(action=…)` + raw DuckDB.
- `context_to_mermaid` graph→mermaid visualization is now **homeless** (died with `cjm-graph-plugin-system`). Per user (2026-06-18): **reconsider/redesign graph-viz from first principles**, do not relocate the old util.
