# Changelog

Mnemosyne uses [Simple Versioning](https://gist.github.com/jonlow/6f7610566408a8efaa4a):
given a version number **MAJOR.MINOR**, increment the:

- **MINOR** after every iteration of development, testing, and quality assurance.
- **MAJOR** for the first production release (1.0) or for significant new functionality (2.0, 3.0, etc.).

---

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
