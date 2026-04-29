# Changelog

See [CHANGELOG.md](../CHANGELOG.md) in the repository root for the full version history.

## Recent Releases

### 2.0

- **Add:** Entity extraction with Levenshtein fuzzy matching (Phase 1)
- **Add:** LLM-driven structured fact extraction with fallback chain (Phase 2)
- **Add:** Temporal recall with exponential decay scoring (Phase 3)
- **Add:** Configurable hybrid scoring weights (Phase 4)
- **Add:** Memory banks for per-domain SQLite isolation (Phase 5)
- **Add:** MCP server with 6 tools, stdio + SSE transports (Phase 6)
- **Add:** Streaming event bus, pattern detection, plugin system (Phase 8)
- **Add:** SQLite WAL mode + busy timeout for concurrency
- **Fix:** Test mocking for extraction fallback (env vars don't affect module-level constants)
- **Tests:** 292 tests passing. Zero failures.

### 1.13.0

- **Add:** Temporal query capabilities — `recall(from_date="...", to_date="...", source="...", topic="...")`
- **Add:** Auto temporal triples on `remember()` — `(memory_id, occurred_on, YYYY-MM-DD)` and `(memory_id, has_source, source)`
- **Fix:** `UnboundLocalError` in fallback scoring when FTS5 returns empty but temporal filters active
- **Tests:** 6 new temporal query tests. All 25 passing.

### 1.12.0

- **Fix:** Embeddings generated but discarded when sqlite-vec absent — now falls back to `memory_embeddings` table with numpy cosine similarity
- **Add:** `_in_memory_vec_search()` — cosine similarity via numpy when sqlite-vec unavailable
- **Add:** `mnemosyne_diagnose` — PII-safe diagnostic tool for troubleshooting
- **Docs:** Replaced junk files with proper developer documentation

### 1.11.0

- **Fix:** Context overflow on consolidation — `sleep()` now chunks memories to fit the LLM context window
- **Fix:** No remote/API model support — added OpenAI-compatible remote LLM client (`MNEMOSYNE_LLM_BASE_URL`)
- **Add:** `chunk_memories_by_budget()` for token-aware batch splitting
- **Add:** `_call_remote_llm()` with httpx primary and urllib fallback
- **Tests:** 7 new tests (2 for chunking, 5 for remote API). All 24 passing.

### 1.10.2

- **Add:** `mnemosyne_update` and `mnemosyne_forget` tools for full CRUD in Hermes plugin
- **Fix:** Auto-sleep dict key, module-level `remember()` signature, BEAM sync on update
- **Fix:** sqlite-vec KNN query LIMIT parameter for vec virtual table planner
- **Fix:** Triple tools in MemoryProvider (missing module-level functions)
- **Remove:** 307 lines of dead code (unused quantization functions, ghost imports)

### 1.10.0

- **Add:** `hermes mnemosyne stats --global` — cross-session working memory stats

### 1.9.0

- **Release:** PyPI package `mnemosyne-memory` now live
- **CI:** GitHub Actions for tests (Python 3.9–3.12) and automated releases
- **Packaging:** Modern `pyproject.toml` with PEP 517

### 1.7

- **Fix:** Subagent context writes polluting persistent memory
- **Fix:** Cross-session recall consistency with global scope preservation
- **Fix:** Fallback keyword scoring for Chinese and spaceless languages

### 1.5

- **Fix:** 6 critical bugs (stats, recall tracking, vector similarity, hardcoded session_id)

### 1.0

First major release. Production-ready.

- BEAM architecture (working + episodic + scratchpad)
- Native vector search via sqlite-vec
- FTS5 full-text hybrid search
- Temporal triples (knowledge graph)
- Hermes plugin integration
- Sub-millisecond latency on CPU

---

For the complete history, see [CHANGELOG.md](../CHANGELOG.md).
For releases, see [GitHub Releases](https://github.com/AxDSan/mnemosyne/releases).
