# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Simple Versioning](https://github.com/AxDSan/mnemosyne) (MAJOR.MINOR).

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

### Removed

- Stale v1 project archived from `/root/.hermes/mnemosyne/` → `mnemosyne-v1-archived/`
- Fabricated APIs from comparison page (removed in commit `b00952e`)

### Tests

- 292 tests passing (up from unknown baseline)
- New test files: `test_entities.py`, `test_entity_integration.py`, `test_banks.py`, `test_mcp_tools.py`, `test_streaming.py`, `test_temporal_recall.py`
- All test tearDown methods handle WAL `-wal`/`-shm` files

---

## [1.x] — Pre-2.0 releases

Initial Mnemosyne releases with basic `remember()`/`recall()`/`sleep()` cycle, SQLite + fastembed embeddings, Hermes plugin registration, and AAAK compression.

[2.0]: https://github.com/AxDSan/mnemosyne/releases/tag/v2.0
