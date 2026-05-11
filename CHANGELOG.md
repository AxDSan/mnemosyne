     1|## [2.6] — 2026-05-11
     2|# Changelog
     3|
     4|All notable changes to this project will be documented in this file.
     5|
     6|The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
     7|and this project adheres to [Simple Versioning](https://github.com/AxDSan/mnemosyne) (MAJOR.MINOR).
     8|
     9|## [Unreleased]
    10|
    11|<<<<<<< HEAD
    12|### Changed
    13|
    14|**E1 — BEAM benchmark adapter uses the real ingest pipeline (additive)**
    15|- `tools/evaluate_beam_end_to_end.py::ingest_conversation` no longer runs a destructive `Batch N: first_3_msg[:100chars]` template summary + DELETE per batch. Per-batch the adapter now backdates the just-inserted `working_memory` rows past sleep's TTL/2 cutoff, then calls `beam.sleep()` — which (post-E3) produces real LLM-generated (or AAAK-fallback) summaries on top of preserved originals.
    16|- Net effect: ~99% of message content was previously discarded at ingest time, leaving every experiment arm running on a corpus of ~500 episodic rows of mostly-empty template strings instead of the actual benchmark messages. Post-E1 the corpus is preserved in `working_memory` AND consolidated summaries land in `episodic_memory` — recall reaches actual content. Unblocks every BEAM-recovery experiment arm.
    17|- Behavior change for benchmark stats output: `stats["wm_count"]` now grows monotonically with input message count (pre-E1 it was always 0 after each batch because the destructive consolidation deleted everything). This is the contract the experiment actually wants to measure.
    18|- **Stacks on E3** (PR #73): depends on additive sleep being merged first; without E3, `beam.sleep()` would still DELETE source rows and the fix would be moot.
    19|
    20|**E3 — Additive sleep (kill summarize-and-delete)**
    21|- `BeamMemory.sleep()` no longer DELETEs source `working_memory` rows after writing the consolidated summary to `episodic_memory`. Originals are marked with a new `consolidated_at` timestamp and remain queryable through recall.
    22|- Maintainer decision (2026-05-10): "Originals stay. Summaries become enrichment on top. Storage cost is fine — it's the lowest-cost tradeoff." Unblocks experiment Arm B (ADD-only ingest) of the BEAM-recovery experiment.
    23|- No feature flag — additive is the only mode going forward.
    24|- `BeamMemory._trim_working_memory` exempts rows with `consolidated_at IS NOT NULL`. The TTL/MAX_ITEMS sliding window only bounds NOT-YET-consolidated content; consolidated originals live until explicit `forget()`. Strict reading of the "originals stay" contract.
    25|- `BeamMemory.remember`'s dedup-update path clears `consolidated_at = NULL` when re-asserting an already-consolidated row, so the refreshed occurrence becomes eligible for sleep again.
    26|- `sleep()` now uses an atomic claim: marks `consolidated_at` BEFORE writing the episodic summary, gated on `consolidated_at IS STILL NULL`. Concurrent sleep callers see `rowcount=0` and bail; crash-after-claim leaves an orphan marker but no phantom summary.
    27|- The `wm_au` FTS trigger now fires `AFTER UPDATE OF content`, not on every UPDATE. Sleep's marker writes don't churn the FTS index — important now that sleep is high-volume.
    28|
    29|### Added
    30|- **E5 PolyphonicRecallEngine** — Multi-voice recall fusion (vector, semantic, temporal, veracity) weighted by a tunable cross-voice scoring matrix
    31|  - Feature-gated: set `MNEMOSYNE_POLYPHONIC_RECALL=1` to enable; disabled by default
    32|  - Weight matrix baked into `polyphonic_recall.py`; no extra deps, no ML model behind a flag
    33|  - Falls back to standard recall path when disabled; feature-flag gates the import, not the call site
    34|
    35|- **C4 recall path provenance diagnostics** — Callers can inspect which recall paths returned each result
    36|  - `recall(source_breakdown=True)` returns per-voice counts and vector coverage stats
    37|  - New `recall_diagnostics.py` module with `compute_recall_path_coverage()` helper
    38|  - Backward compatible: existing `recall()` calls are unaffected (default: no breakdown)
    39|
    40|- **C13.b fact-extraction failure diagnostics** — Per-extraction failure counters and sentiment-bias detection
    41|  - Each extraction attempt now records `attempts`, `failures`, and `last_error` counters
    42|  - New `mnemosyne/extraction/diagnostics.py` with `analyze_extraction_health()` and `detect_sentiment_bias()`
    43|  - Extraction pipeline no longer silently swallows repeated failures
    44|
    45|### Fixed
    46|- **C25 + docs: DeltaSync table/column allowlist guards schema drift**
    47|  - Previously DeltaSync blindly mirrored all tables/columns; any upstream schema drift would replicate into beam
    48|  - Now uses a hardcoded allowlist of `table → [columns]` for working_memory, episodic_memory, scratchpad, knowledge_graph, and memory_embeddings
    49|  - Snapshot DBs or upstream changes adding columns (e.g., `wm.x_new_field`) are silently dropped on the receiving side
    50|  - Requires no server-side changes (DeltaSync wire protocol unchanged); only the receiving end enforces the allowlist
    51|  - See `docs/api-reference.md` (expanded DeltaSync section) and migration notes in `UPDATING.md`
    52|
    53|- **E3 additive sleep**
    54|  - `sleep()` no longer **deletes** consolidated working memory rows — it marks them with `consolidated_at`
    55|  - Originals remain queryable and searchable alongside the new episodic summary
    56|  - Backward compatible: pre-E3 DBs gain the column automatically at startup; old rows are backfilled to skip re-summarization
    57|  - Working memory trim now exempts already-consolidated rows so the "originals stay" contract survives TTL window expiry
    58|  - Re-remembering a consolidated row clears `consolidated_at` so it re-enters the sleep queue (avoids permanent skip)
    59|
    60|- **PII-safe diagnose —fix flag**
    61|  - `mnemosyne diagnose --fix` auto-installs missing dependencies (fastembed, sqlite-vec, numpy, huggingface_hub)
    62|  - `mnemosyne doctor` alias for quick access
    63|  - `--dry-run` preview mode shows what would be installed without downloading anything
    64|
    65|- **deploy_hermes_provider.sh fix for curl | bash**
    66|  - No more `BASH_SOURCE[0]: unbound variable` error
    67|  - Piped install now auto-clones the repo to `~/.hermes/mnemosyne-repo/`
    68|  - Backward compatible: local clones still work as before
    69|
    70|### Upgraded
    71|- **E4 veracity threading** — working-memory recall multiplier now integrates per-row veracity scores
    72|  - `remember_batch()` propagates veracity into each working memory insertion
    73|  - The `recall()` path applies a veracity multiplier (`wm_vm`) to working-memory tier results, so trusted rows rank higher
    74|
    75|- **E1 benchmark adapter** — ICLR 2026 BEAM benchmark now runs the real ingest pipeline instead of a toy adapter
    76|  - No more skipped transforms: benchmarks exercise the same code path as production
    77|  - Stacks on PR #73 (additive sleep) for backfill compatibility
    78|  - `tools/evaluate_beam_end_to_end.py` upgraded with full-pipeline harness
    79|
    80|- **/review hardening**
    81|  - Post-filter counters: `kept-not-scanned`, `post_filter_removed`, rate clamp diagnostics
    82|  - `kept_not_scanned` tracks vector-only hits pulled from pool but never score-tested (valuable coverage metric)
    83|=======
    84|### Fixed
    85|
    86|**E6 — TripleStore silent-destruction bug**
    87|- `TripleStore.add()` auto-invalidates rows with matching `(subject, predicate)` regardless of `object`. Every production write used annotation semantics (`(memory_id, "mentions", entity)`, `(memory_id, "fact", text)`, etc.), so each new annotation for a memory silently set `valid_until` on prior annotation rows with the same key. Effect: entity / fact graphs on each Mnemosyne database have lost data any time a memory had more than one entity or fact extracted.
    88|- Fix splits storage into two purpose-specific tables:
    89|  - `triples` table retains current-truth temporal semantics with auto-invalidation, suitable for facts like `(user, prefers, X)` later superseded by `(user, prefers, Y)`. No production caller writes here today; the table is preserved for future use.
    90|  - New `annotations` table (`mnemosyne/core/annotations.py`, `AnnotationStore`) is append-only and now hosts `mentions`, `fact`, `occurred_on`, `has_source` — all multi-valued by design.
    91|- Production call sites migrated to `AnnotationStore`:
    92|  - `BeamMemory._extract_and_store_entities`, `_extract_and_store_facts`, `_add_temporal_triple`
    93|  - `BeamMemory._find_memories_by_entity`, `_find_memories_by_fact`
    94|  - `Mnemosyne.remember(extract_entities=True)` and `Mnemosyne.remember(extract=True)`
    95|- **Auto-migration on first BeamMemory init.** Existing databases auto-migrate annotation-flavored rows from `triples` to `annotations` with a backup written to `{db}.pre_e6_backup`. Set `MNEMOSYNE_AUTO_MIGRATE=0` to disable auto-migration and run `python scripts/migrate_triplestore_split.py` manually instead.
    96|- **`TripleStore.add_facts()` is deprecated.** Emits `DeprecationWarning`; legacy write behavior preserved for backward compatibility. New code should call `AnnotationStore.add_many(memory_id, "fact", facts)` directly.
    97|
    98|### Added
    99|
   100|- `mnemosyne/core/annotations.py` — `AnnotationStore` class + `ANNOTATION_KINDS` constant (`mentions`, `fact`, `occurred_on`, `has_source`)
   101|- `scripts/migrate_triplestore_split.py` — idempotent, transactional, file-level-backup migration script with `--dry-run`, `--no-backup`, `--db PATH` flags
   102|- `MNEMOSYNE_AUTO_MIGRATE` env var (default `1`; set to `0` for explicit operator control)
   103|- `scripts/mnemosyne-stats.py` — new `annotations` section in JSON output alongside the existing `triples` section
   104|- 30+ new tests covering the new store, the migration script, the auto-migrate hook, and end-to-end production-path regression guards
   105|>>>>>>> b373da0 (docs(e6): UPDATING.md + CHANGELOG.md entries for TripleStore split)
   106|
   107|## [2.5] — 2026-05-10
   108|
   109|### Added
   110|- Working memory TTL: `mnemosyne update <id> --ttl 7d` expires rows after the given interval
   111|- Dedicated WHL builds in the public release asset pipeline (CI matrix + install smoke test)
   112|- `heal_quality` pipeline: LLM-judge based quality assessment for episodic summaries (factual density, format compliance, grounding)
   113|- `mnemosyne_invalidate` memory tool + forget cascade (deletes source reference + down-weighted/fuzzy matches)
   114|- ICLR 2026 BEAM SOTA benchmark harness (compare Mnemosyne against Honcho, Hindsight, LIGHT, Mem0, LangMem, Zep, Memobase)
   115|- SOTA results page on docs site with BEAM tier badges
   116|
   117|### Changed
   118|- Episodic consolidation quality significantly improved (LLM judge enforces factual density ≥100B, strict grounding)
   119|- Dimensionality-aware vector comparison (binary uses hamming, float uses cosine)
   120|- Episodic memory recall correctly handles `valid_until` and `superseded_by` filters
   121|- Memory embeddings: `memory_id` uniqueness constraint prevents double-registration
   122|- BEAM benchmark now runs the full ingest pipeline instead of a simple adapter
   123|
   124|### Fixed
   125|- Export `--from` semantics: BEAM export correctly uses `export_from` path with detached-db copy
   126|- RRF (Reciprocal Rank Fusion) hybrid scoring with k=60 fuses 4 retrieval voices
   127|- Ghost session bug: `sleep_all_sessions()` now correctly discovers sessions containing NULL `session_id` rows
   128|- `--version` flag now shows the correct package version string
   129|
