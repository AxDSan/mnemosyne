<<<<<<< HEAD
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
   105|>>>>>>> pr70
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
=======
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Simple Versioning](https://github.com/AxDSan/mnemosyne) (MAJOR.MINOR).

## [Unreleased]

### Fixed

**E6.a — follow-up gaps surfaced by the E6 review**
- `Mnemosyne.forget()` and `BeamMemory.forget_working()` now cascade-delete annotations for the forgotten memory_id. Pre-fix, `mentions` / `fact` / `occurred_on` / `has_source` rows stayed in the annotations table after forget — they leaked through `export_to_file`, kept surfacing in `_find_memories_by_entity` and `_find_memories_by_fact`, and remained queryable through MCP tools. Privacy regression introduced by E6 (annotations table didn't exist pre-E6, so the cascade gap is new).
- `mnemosyne_triple_add` MCP tool now routes annotation-flavored predicates (`mentions`, `fact`, `occurred_on`, `has_source`) to `AnnotationStore.add()` instead of `TripleStore.add()`. Pre-fix, an agent calling the tool with `predicate="mentions"` would silently invalidate prior `(subject, "mentions")` annotation rows via the same auto-invalidation bug E6 was designed to fix — the bug remained reachable from the MCP layer. Current-truth predicates (anything outside `ANNOTATION_KINDS`) still route to `TripleStore` for backward compatibility.

**E6 — TripleStore silent-destruction bug**
- `TripleStore.add()` auto-invalidates rows with matching `(subject, predicate)` regardless of `object`. Every production write used annotation semantics (`(memory_id, "mentions", entity)`, `(memory_id, "fact", text)`, etc.), so each new annotation for a memory silently set `valid_until` on prior annotation rows with the same key. Effect: entity / fact graphs on each Mnemosyne database have lost data any time a memory had more than one entity or fact extracted.
- Fix splits storage into two purpose-specific tables:
  - `triples` table retains current-truth temporal semantics with auto-invalidation, suitable for facts like `(user, prefers, X)` later superseded by `(user, prefers, Y)`. No production caller writes here today; the table is preserved for future use.
  - New `annotations` table (`mnemosyne/core/annotations.py`, `AnnotationStore`) is append-only and now hosts `mentions`, `fact`, `occurred_on`, `has_source` — all multi-valued by design.
- Production call sites migrated to `AnnotationStore`:
  - `BeamMemory._extract_and_store_entities`, `_extract_and_store_facts`, `_add_temporal_triple`
  - `BeamMemory._find_memories_by_entity`, `_find_memories_by_fact`
  - `Mnemosyne.remember(extract_entities=True)` and `Mnemosyne.remember(extract=True)`
- **Auto-migration on first BeamMemory init.** Existing databases auto-migrate annotation-flavored rows from `triples` to `annotations` with a backup written to `{db}.pre_e6_backup`. Set `MNEMOSYNE_AUTO_MIGRATE=0` to disable auto-migration and run `python scripts/migrate_triplestore_split.py` manually instead.
- **`TripleStore.add_facts()` is deprecated.** Emits `DeprecationWarning`; legacy write behavior preserved for backward compatibility. New code should call `AnnotationStore.add_many(memory_id, "fact", facts)` directly.

### Added

- `mnemosyne/core/annotations.py` — `AnnotationStore` class + `ANNOTATION_KINDS` constant (`mentions`, `fact`, `occurred_on`, `has_source`)
- `scripts/migrate_triplestore_split.py` — idempotent, transactional, file-level-backup migration script with `--dry-run`, `--no-backup`, `--db PATH` flags
- `MNEMOSYNE_AUTO_MIGRATE` env var (default `1`; set to `0` for explicit operator control)
- `scripts/mnemosyne-stats.py` — new `annotations` section in JSON output alongside the existing `triples` section
- 30+ new tests covering the new store, the migration script, the auto-migrate hook, and end-to-end production-path regression guards

## [2.5] — 2026-05-10

### Added

**NAI-0 Algorithmic Sprint**
- `BeamMemory.format_context(results, format="bullet"|"json")` — structured context formatting
- `BeamMemory._sandwich_order()` — U-shaped attention ordering (high-first, medium-middle, high-last)
- `BeamMemory._fact_line()` — clean one-line fact format with date, source, confidence
- `BeamMemory._format_context_json()` / `_format_context_bullet()` — JSON and markdown output
- RRF (Reciprocal Rank Fusion) in `PolyphonicRecallEngine._combine_voices()` with k=60 constant
- Covering indexes: `idx_em_scope_imp`, `idx_wm_session_recall`, `idx_mem_emb_type`
- `tools/bench_nai0.py` — minimal 20-question benchmark for quick before/after measurement

**Self-Healing Quality Pipeline** (`scripts/heal_quality.py`, PR #67 by ether-btc)
- Detects degraded episodic memory entries (bullet-format, <300 chars) and repairs them via a 4-stage LLM-as-Judge closed loop: Extract → Generate → Judge → Repair
- Fault taxonomy: `truncated`, `generic`, `missing_facts`, `wrong_format`
- Judge scores 4 dimensions (factual density, format compliance, length sufficiency, grounding) each 0-100
- Repair strategies are fault-specific: context doubling, specificity enforcement, fact injection, format rewrite
- Loop with `MAX_RETRIES` (default 3) and automatic escalation to stronger model after 2 failures
- Quality provenance in `metadata_json`: `quality_score`, `judge_model`, `consolidated_at`, `fault_before_repair`, `retry_loop_count`
- Configurable via env: `MNEMOSYNE_HEAL_JUDGE_THRESHOLD`, `MNEMOSYNE_HEAL_MAX_RETRIES`, `MNEMOSYNE_HEAL_MIN_LEN`, `MNEMOSYNE_HEAL_BUDGET`, `MNEMOSYNE_HEAL_ESCALATE_AFTER`
- Works with any LLM backend (MiniMax M2.7 via mmx-cli, local GGUF, or remote OpenAI-compatible API)
- CLI: `python scripts/heal_quality.py [--detect-only] [--entry-id ID] [--dry-run]`

**Chunked LLM Summarization** (`mnemosyne/core/local_llm.py`)
- Splits large memory lists into context-window-sized chunks before summarization
- Two-pass: summarize each chunk individually, then consolidate chunk summaries
- Fixes truncation issues with smaller models (Qwen2.5-1.5B) on large sessions

### Changed
- `BeamMemory.recall()` default `top_k`: 5 → 40
- Polyphonic recall voice combination: weighted average → position-based RRF
- `mnemosyne/__init__.py`: version bump to 2.5.0

## [2.4] — 2026-05-07

### Added

**Hindsight Importer — migrate FROM Hindsight INTO Mnemosyne**
- New `HindsightImporter` class in `mnemosyne/core/importers/hindsight.py`
- Import from Hindsight JSON exports OR live Hindsight HTTP API (`/v1/default/banks/{bank}/memories/list`)
- Writes directly to `episodic_memory` (not working memory) — preserves original timestamps, fact types, session grouping, metadata, scope, and veracity
- Stable duplicate skipping via SHA256-based IDs (`hs_` prefix)
- Importance scoring derived from Hindsight `fact_type` (world=0.75, experience=0.65, observation=0.55) + proof_count bonus
- Full metadata preservation: hindsight_id, fact_type, context, dates, entities, chunk_id, tags, consolidation timestamps
- CLI: `mnemosyne import-hindsight <file.json|url> [bank]`
- Registered in provider registry alongside Mem0, Letta, Zep, Cognee, Honcho, SuperMemory
- 102 lines of regression tests: timestamp preservation, episodic-only import, stable duplicate skipping, FTS indexing, provider-registry usage

**Host LLM Adapter — route consolidation through Hermes' authenticated provider**
- New `mnemosyne/core/llm_backends.py` — tiny `LLMBackend` Protocol (one method: `complete()`), process-global registry, `CallableLLMBackend` dataclass for tests
- New `hermes_memory_provider/hermes_llm_adapter.py` — `HermesAuxLLMBackend` routes through `agent.auxiliary_client.call_llm(task="compression", ...)`
- `MnemosyneMemoryProvider.initialize()` registers the backend; `shutdown()` unregisters it with a brief drain for in-flight threads
- `summarize_memories()` and `extract_facts()` consult host first when `MNEMOSYNE_HOST_LLM_ENABLED=true`
- **Host-skips-remote rule (A3):** When host attempt produces no usable text, remote URL is skipped — falls straight to local GGUF. Prevents stale URL leaks.
- `llm_available()` returns `True` when host backend is registered, so Hermes-only users don't get short-circuited by `beam.sleep()`
- `on_session_end()` runs sleep in daemon thread with 15s join timeout; `shutdown()` drains 2s before unregistering
- Fact extraction uses `temperature=0.0` for determinism; consolidation stays at `0.3`
- 7 new tests covering registry round-trip, host-route precedence, A3 skip-remote rule, gate semantics, shutdown drain race, daemon exception logging, bullet-list output preservation
- Live end-to-end verified with `openai-codex` OAuth subscription through ChatGPT backend

### Why this matters

**Hindsight importer:** Before this, migrating FROM Hindsight required going through `remember()`, which assigned current timestamps and wrote to working memory. Historical memories lost their original context. Now Hindsight migrations preserve the full temporal record with zero data loss.

**Host LLM adapter:** Hermes users on OAuth-backed providers (ChatGPT/Codex subscriptions) could not use Mnemosyne's LLM-backed operations because `MNEMOSYNE_LLM_BASE_URL` expects an OpenAI-compatible API key endpoint, not OAuth. Now they can route through Hermes' already-authenticated auxiliary client with zero extra credentials.

---

## [2.3.1] — 2026-05-06

### Fixed

- **Auto-sleep consolidation blocks TUI agent**: `_maybe_auto_sleep()` now runs in a background thread with a 5-second timeout instead of synchronously. Local LLM summarization (ctransformers) can no longer hang the agent worker thread. (#23)
- `MNEMOSYNE_AUTO_SLEEP_ENABLED` env var now controls auto-sleep behavior. Default is `false` (disabled) for interactive safety. Set to `true` to re-enable.
- Config schema updated to reflect new default.

## [2.3] — 2026-05-05

### Added

**Tiered Episodic Degradation — long-term recall without unbounded growth**
- Three degradation tiers: Tier 1 (0-30d, full detail), Tier 2 (30-180d, LLM-compressed), Tier 3 (180d+, entity-extracted signal)
- Automatic tier promotion during `sleep()` — no manual maintenance
- Tier multipliers in recall scoring: cold memories need 4x stronger semantic match
- Configurable via `MNEMOSYNE_TIER2_DAYS`, `MNEMOSYNE_TIER3_DAYS`, `MNEMOSYNE_TIER*_WEIGHT`
- Mnemonics can now truthfully claim "remembers what you told it a year ago"

**Smart Compression — entity-aware tier 2→3 extraction**
- `_extract_key_signal()` scores sentences by entity density (proper nouns, acronyms, security terms, tech stack, urgency)
- Preserves facts buried anywhere in a long memory, not just the first sentence
- Configurable: `MNEMOSYNE_SMART_COMPRESS=1` (default on), `MNEMOSYNE_TIER3_MAX_CHARS=300`

**Memory Confidence — veracity signal for every memory**
- New `veracity` field: `stated`, `inferred`, `tool`, `imported`, `unknown`
- `remember(veracity="stated")` — set confidence at write time
- `recall(veracity="stated")` — filter by confidence level
- Recall applies veracity multiplier to scores (stated=1.0x, inferred=0.7x, tool=0.5x)
- `get_contaminated()` — surface non-stated memories for review
- Configurable weights via `MNEMOSYNE_*_WEIGHT` env vars

### Fixed
- `local_llm.summarize()` → `summarize_memories()` — would crash on LLM degradation path
- SQLite connection conflicts in batch degradation tests
- Removed hallucinated Phase 2 from roadmap

## [2.2] — 2026-05-02

### Added

**Cross-Provider Importers — migrate from any memory platform**
- New `mnemosyne/core/importers/` module with 6 provider importers
- **Mem0:** SDK pagination → REST → structured export fallback chain; preserves user/agent/app scoping
- **Letta (MemGPT):** AgentFile `.af` format parsing (JSON/YAML/TOML); memory blocks → working_memory, messages → episodic
- **Zep:** users → sessions → `memory.get()` per-session iteration; messages + summaries + facts extraction
- **Cognee:** `get_graph_data()` nodes/edges extraction; nodes → episodic memories, edges → triples
- **Honcho:** peers → sessions → `context()` + messages; peer identity preserved as author_id
- **SuperMemory:** `documents.list()` + `search.execute()`; container tags mapped to channel_id
- **Agentic importer:** generates ready-to-run Python migration scripts and AI agent instructions for all 6 providers

**CLI: `hermes mnemosyne import` extended**
- `--from <provider>` — import directly from Mem0, Letta, Zep, etc.
- `--list-providers` — show all supported providers with docs links
- `--generate-script` — generate a migration script for any provider
- `--agentic` — output instructions to give your AI agent for extraction
- `--dry-run` — validate and transform without writing

**Plugin tool updated**
- `mnemosyne_import` schema extended with `provider`, `api_key`, `user_id`, `agent_id`, `dry_run`, `channel_id` params

### Changed

- README: added "Migrate from other memory providers" section with examples

## [2.1] — 2026-05-02

### Added

**Multi-Agent Identity Layer**
- New columns `author_id`, `author_type`, `channel_id` on `working_memory` and `episodic_memory` with indexes
- `Mnemosyne(author_id=..., author_type=..., channel_id=...)` constructor params
- `remember()` auto-populates identity columns from session context
- `recall(author_id=..., author_type=..., channel_id=...)` filter params
- `get_stats(author_id=..., author_type=..., channel_id=...)` filter params
- Cross-session channel recall: when `channel_id` is provided, scope expands to include all memories in that channel regardless of session
- MCP server: per-connection instances replace module-level cache; identity via tool args or env vars (`MNEMOSYNE_AUTHOR_ID`, `MNEMOSYNE_AUTHOR_TYPE`, `MNEMOSYNE_CHANNEL_ID`)
- Hermes plugin `_get_memory()` reads identity from environment variables

### Changed
- MCP `_get_instance()` renamed to `_create_instance()` — creates fresh instances per connection
- Episodic memory SELECTs and recall-tracking UPDATEs use dynamic session/channel scope

## [2.0] — 2026-04-29

### Added

**Phase 1: Entity Sketching**
- Regex-based entity extraction (`@mentions`, `#hashtags`, quoted phrases, capitalized sequences)
- Pure-Python Levenshtein distance with O(min) space optimization
- Fuzzy entity matching with prefix/substring bonuses and configurable threshold
- `extract_entities=True` parameter on `remember()` — backward compatible, default False

**Phase 2: Structured Fact Extraction**
- LLM-driven fact extraction via `extract_facts()` and `extract_facts_safe()`
- Graceful fallback chain: remote OpenAI-compatible API → local ctransformers GGUF → skip
- Fact parsing with numbering/bullet cleanup, length filter, cap at 5 facts

**Phase 3: Temporal Recall**
- Exponential decay temporal scoring: `exp(-hours_delta / halflife)`
- `temporal_weight`, `query_time`, `temporal_halflife` parameters on `recall()`
- Environment variable `MNEMOSYNE_TEMPORAL_HALFLIFE_HOURS` for global default
- Temporal boost applied across all recall tiers (working, episodic, entity, fact)

**Phase 4: Configurable Hybrid Scoring**
- User-tunable scoring weights: `vec_weight`, `fts_weight`, `importance_weight`
- `_normalize_weights()` with env var fallback and sensible defaults (50/30/20)
- Per-query weight overrides without global state mutation

**Phase 5: Memory Banks**
- `BankManager` class for named namespace isolation
- Per-bank SQLite files under `banks/<name>/mnemosyne.db`
- Bank operations: create, delete, list, rename, exists check, stats
- `Mnemosyne(bank="work")` constructor parameter
- Bank name validation (alphanumeric + hyphens/underscores, max 64 chars)

**Phase 6: MCP Server**
- Model Context Protocol server with 6 tools
- stdio transport (Claude Desktop, etc.) and SSE transport (web clients)
- Per-bank instance caching
- CLI entry: `mnemosyne mcp`

**Phase 7: Hermes Agent Integration**
- 15 Hermes tools: remember, recall, stats, triple_add, triple_query, sleep, scratchpad_write/read/clear, invalidate, export, update, forget, import, diagnose
- 3 lifecycle hooks: `pre_llm_call` (context injection), `on_session_start`, `post_tool_call`
- AAAK compression for context injection
- Session-aware memory instances

**Phase 8: v2 Differentiation**
- `MemoryStream` — push (callbacks) and pull (iterator) event stream, thread-safe
- `DeltaSync` — checkpoint-based incremental synchronization between instances
- `MemoryCompressor` — dictionary-based, RLE, and semantic compression
- `PatternDetector` — temporal (hour/weekday), content (keyword, co-occurrence), sequence patterns
- `MnemosynePlugin` ABC with 4 lifecycle hooks
- `PluginManager` with auto-discovery from `~/.hermes/mnemosyne/plugins/`
- 3 built-in plugins: `LoggingPlugin`, `MetricsPlugin`, `FilterPlugin`

### Changed

- **CLI rewritten** — all commands now use v2 `Mnemosyne`/`BeamMemory` instead of stale v1 `MnemosyneCore`
- **SQLite WAL mode** — both `memory.py` and `beam.py` now use WAL journal mode with 5s busy timeout for better concurrency
- **FastEmbed cache** — model cache persists at `~/.hermes/cache/fastembed` instead of ephemeral `/tmp`
- **Legacy dual-write** — uses `INSERT OR REPLACE` for dedup safety

### Fixed

- `cli.py` DATA_DIR hardcoded to stale v1 path — now uses `MNEMOSYNE_DATA_DIR` env var
- Duplicate `_recency_decay()` definitions in `beam.py` merged into single function
- SQLite concurrency test failures — WAL mode + proper tearDown cleanup
- `plugin.yaml` declared only 9 of 15 tools — now declares all 15

### Tests

- 292 tests passing (up from unknown baseline)
- New test files: `test_entities.py`, `test_entity_integration.py`, `test_banks.py`, `test_mcp_tools.py`, `test_streaming.py`, `test_temporal_recall.py`
- All test tearDown methods handle WAL `-wal`/`-shm` files

---

## [1.13] — 2026-04-28

### Added

- **Temporal queries** — query the knowledge graph with time awareness (`temporal_halflife`, `temporal_weight`)
- **Memory bank isolation** — separate namespaces for different projects or contexts
- **Configurable hybrid scoring** — tune vector vs. FTS vs. importance weights per query
- **PII-safe diagnostic tool** (`mnemosyne_diagnose`) — inspect your memory without exposing sensitive data

### Fixed

- `sqlite-vec` LIMIT parameter handling
- Triples module-level helpers
- Embeddings fallback when `sqlite-vec` is absent
- Memory embeddings table auto-creation for sqlite-vec fallback

---

## [1.12] — 2026-04-26

### Added

- **Feature comparison matrix** vs. cloud providers (Honcho, Zep, Mem0, Hindsight)
- **DevOps policy** — comprehensive procedures for releases, security, and operations

### Changed

- Documentation cleanup — replaced placeholder files with proper repo docs

---

## [1.11] — 2026-04-25

### Added

- **Token-aware batch sizing** in consolidation — no more OOM on large memory sets
- **Remote API support** for LLM summarization in `sleep()`

### Fixed

- Consolidation edge cases with mixed local/remote LLM configs

---

## [1.10] — 2026-04-24

### Added

- **`mnemosyne_update` tool** — modify existing memories without full replacement
- **`mnemosyne_forget` tool** — targeted memory deletion
- **Global stats flag** — `hermes mnemosyne stats --global` for workspace-wide metrics

### Fixed

- Working memory scope handling across sessions (PR #11)
- Default scope set to 'global' for migrated memories
- Working memory stats and recall tracking consistency

---

## [1.9] — 2026-04-23

### Added

- **PyPI release** — `pip install mnemosyne-memory` works out of the box
- **CI/CD pipeline** — GitHub Actions for testing and release automation
- **`pyproject.toml`** — modern Python packaging
- **UPDATING.md** — migration guide for existing users

### Fixed

- Plugin `register()` export for Hermes plugin loader discovery
- Cross-session recall inconsistency (Issue #7, Bug 2)
- Subagent context write blocking (PR #8)

---

## [1.8] — 2026-04-22

### Added

- **Plugin auto-discovery** — `register()` method for Hermes plugin CLI
- **Bug report template** — official GitHub issue template

### Fixed

- 6 bugs from Issue #6 — edge cases in recall, scope handling, and tool registration

---

## [1.7] — 2026-04-22

### Added

- **PEP 668 PSA** — documentation for Ubuntu 24.04 / Debian 12 users hitting `externally-managed-environment`

### Fixed

- Provider `register_cli` using nested parser instead of subparser
- `sys.path` injection with graceful `ImportError` fallback

---

## [1.6] — 2026-04-21

### Added

- **Feature request template** — GitHub issue template for enhancements
- **Simple versioning** adopted — MAJOR.MINOR instead of semver

### Fixed

- `fastembed` dependency correction (was incorrectly listing `sentence-transformers`)
- Benchmarks restored to README with LongMemEval scores

---

## [1.5] — 2026-04-20

### Added

- **Export/import** — cross-machine memory migration (`mnemosyne_export` / `mnemosyne_import`)
- **One-command installer** — `curl | bash` setup for new users
- **MemoryProvider mode** — deploy Mnemosyne as a standalone memory provider via plugin system
- **Anchored table of contents** in README

### Changed

- README fully rewritten — professional, community-focused, removed bloat
- FluxSpeak branding removed from LICENSE and metadata (Mnemosyne is its own thing)

---

## [1.4] — 2026-04-19

### Added

- **Temporal validity** — memories can have expiration dates
- **Global scope** — memories visible across all sessions
- **Local LLM-based sleep()** — summarization without cloud APIs
- **Recall tracking** — knows what you already remembered
- **Recency decay** — older memories naturally fade in relevance

### Fixed

- Path type bug in memory override skill
- `plugin.yaml` moved to repo root for Hermes compatibility

---

## [1.3] — 2026-04-17

### Added

- **Memory override skill** — bake memory into pre_llm_call and session_start hooks
- **Critical deprecation notice** for legacy memory tool

---

## [1.2] — 2026-04-13

### Added

- **Scale limits** — tested and documented for 1M+ token capacity
- **Legacy DB migration script** — upgrade path from early schemas

### Changed

- Auto-logging of `tool_execution` disabled by default (privacy)

---

## [1.1] — 2026-04-10

### Added

- **BEAM architecture** — sqlite-vec + FTS5 + sleep consolidation
- **BEAM benchmarks** — dedicated benchmark suite with published results
- **Dense retrieval** via fastembed
- **AAAK compression** — compressed memory format for context injection
- **Temporal triples** — structured fact storage with subject/predicate/object

### Fixed

- Thread-local connection bug

---

## [1.0] — 2026-04-05

### Added

- **Initial release** — zero-dependency AI memory system
- **`remember()` / `recall()` / `sleep()`** — core memory cycle
- **SQLite + fastembed embeddings** — local vector search
- **Hermes plugin registration** — basic tool integration
- **AAAK compression** — early context compression for token limits

[2.4]: https://github.com/AxDSan/mnemosyne/releases/tag/v2.4
[2.0]: https://github.com/AxDSan/mnemosyne/releases/tag/v2.0
[1.13]: https://github.com/AxDSan/mnemosyne/releases/tag/v1.13
[1.0]: https://github.com/AxDSan/mnemosyne/releases/tag/v1.0
>>>>>>> 83b2dee (fix(e6.a): forget() cascade to annotations + MCP triple_add routing)
