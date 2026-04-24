# Changelog

Mnemosyne uses [Simple Versioning](https://gist.github.com/jonlow/6f7610566408a8efaa4a):
given a version number **MAJOR.MINOR**, increment the:

- **MINOR** after every iteration of development, testing, and quality assurance.
- **MAJOR** for the first production release (1.0) or for significant new functionality (2.0, 3.0, etc.).

---

## 1.10.2

- **Add `mnemosyne_update` and `mnemosyne_forget` tools** — Full CRUD for Hermes plugin. Update content/importance by ID, or hard-delete a memory from both legacy and BEAM tables.
- **Fix auto-sleep dict key** — `count` → `total` so consolidation triggers correctly. (#12 follow-up)
- **Fix module-level `remember()` signature** — Added missing `scope` and `valid_until` params.
- **Fix `update()` BEAM sync** — `Memory.update()` now syncs changes to `working_memory` via new `BeamMemory.update_working()`.
- **Remove dead `--global` flag** — Cleaned `STATS_SCHEMA` and `_handle_stats` of meaningless `global` parameter.
- **Fix split-brain session state** — `hermes_plugin/tools.py` now imports `_get_memory()` / `_get_triples()` from `__init__.py` instead of maintaining separate globals.
- **Remove ghost imports** — Dead `hermes_cli.config` try/except blocks removed from provider and plugin.
- **Dead code removal** — Removed 6 dead quantization functions, `cosine_similarity`, `calculate_relevance`, `deserialize`, and other unused code. (-307 lines)
- **Register missing tool schemas** — `mnemosyne_invalidate`, `mnemosyne_export`, `mnemosyne_import` were defined but never registered.
- **README alignment** — Fixed PyPI badge, VEC_TYPE default (`int8`), Python version (`3.9+`), documented optional REST API.
- **Align TripleStore default DB** — Changed from `~/.mnemosyne` to `~/.hermes/mnemosyne/data` for consistency.
- **Fix `_vec_type_cache` stale risk** — Removed unsafe `id(conn)` cache; queries `sqlite_master` each time.
- **Add DevOps policy** — `DEVOPS.md` covers commit standards, release process, authorship, rollback, security.

## 1.10.1

- **Fix `get_working_stats()`** — Now counts ALL working memories globally, not just current session. (PR #11 by @rakaarwaky)
- **Fix `recall()` tracking UPDATE** — Global memories now correctly increment `recall_count` and `last_recalled` when recalled from other sessions. (PR #11 by @rakaarwaky)
- **Fix column defaults** — `scope` column defaults changed from `'session'` to `'global'` for backward compatibility with pre-scope behavior. (PR #11 by @rakaarwaky)
- **Fix sqlite-vec KNN query** — `_vec_search()` inlined LIMIT parameter because sqlite-vec's virtual table planner requires the limit at query planning time. Fixes `mnemosyne_recall` failure on systems with sqlite-vec installed. (#12)
- **Fix triple tools in MemoryProvider** — Added missing `add_triple()` and `query_triples()` module-level functions to `mnemosyne.core.triples`. Aligned triples database path with BEAM memory so triples share the same SQLite file. (#13)
- **Deprecate `get_global_working_stats()`** — Now aliases `get_working_stats()`. The `--global` CLI flag remains for backward compatibility.

## 1.10.0

- **`hermes mnemosyne stats --global`** — Show working memory stats across all sessions (not just current session). Adds `sessions` count to output.
- **`mnemosyne_stats(global=true)`** — MemoryProvider tool also supports global stats.
- Internal: added `BeamMemory.get_global_working_stats()` for global working memory aggregation.

## 1.9.0

- **PyPI release** — `pip install mnemosyne-memory` is now live: https://pypi.org/project/mnemosyne-memory/
- **Automated releases** — GitHub Actions builds wheels + sdist, creates GitHub releases, and publishes to PyPI on every `v*` tag
- **Trusted publishing** — PyPI publishing via OIDC (no long-lived API tokens)
- **CI pipeline** — Tests run on Python 3.9–3.12 on every PR and push
- **Historical releases** — Retroactively tagged and released v1.0.0, v1.5.0, v1.7.0, and v1.8.0 on GitHub
- **Modern packaging** — Added `pyproject.toml` (PEP 517) with dynamic version from `mnemosyne/__init__.py`
- **Test fix** — Disable LLM summarization in cross-session recall test to prevent Chinese-to-English translation during consolidation

## 1.8

- Fix Hermes plugin CLI discovery: add `register(ctx)` to wire up `hermes mnemosyne stats|sleep|inspect|clear|export|import|version`
- README: clarify deploy script vs pip install paths (Option A / Option B)

## 1.7

- Fix subagent context writes polluting persistent memory (PR #8 by @woaim65)
- Fix cross-session recall inconsistency: global memories now survive consolidation with scope preserved
- Fix fallback keyword scoring for Chinese and spaceless languages (character-level overlap)
- Fix episodic memory having no fallback scan when vector search and FTS5 both miss
- Fix plugin tools singleton using stale session_id across sessions
- Add regression tests: subagent context safety, cross-session recall, Chinese substring matching, session singleton updates

## 1.6

- Feature request issue template
- Documentation improvement issue template
- Issue template config with links to Discussions and Security advisories

## 1.5

- Fix 6 critical bugs from issue #6 (stats, recall tracking, vector similarity, missing methods, hardcoded session_id)
- Fix fastembed dependency in setup.py and README (was incorrectly listing sentence-transformers)
- Official bug report issue template
- Adopt simplified MAJOR.MINOR versioning

## 1.4

- Full README rewrite: professional, community-focused, benchmarks restored
- CONTRIBUTING.md added
- FluxSpeak branding scrubbed (author/metadata corrected)
- Project image banner added

## 1.3

- Export / import memory for cross-machine migration
- CLI subcommands: `export`, `import`, `version`

## 1.2

- Mnemosyne as deployable MemoryProvider via Hermes plugin system
- One-command installer (`python -m mnemosyne.install`)
- CLI fix: `register_cli` correctly handles subparsers

## 1.1

- Dense retrieval via fastembed (bge-small-en-v1.5)
- Temporal validity + global scope (`scope="global"`)
- Recall tracking + recency decay scoring
- Exact-match deduplication in working memory
- Local LLM-based sleep consolidation (TinyLlama fallback)
- BEAM scale limits for 1M+ token capacity

## 1.0

First major release. Production-ready.

- BEAM architecture: working_memory, episodic_memory, scratchpad
- Native vector search via sqlite-vec (HNSW-style)
- FTS5 full-text hybrid search (50% vector + 30% FTS + 20% importance)
- Temporal triples (time-aware knowledge graph)
- AAAK context compression
- Configurable vector compression: float32, int8, bit
- Hermes plugin integration
- Sub-millisecond latency on CPU
