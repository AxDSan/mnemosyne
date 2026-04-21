# Mnemosyne

![Mnemosyne](/assets/mnemosyne.jpg)

> Native, zero-cloud memory for AI agents. SQLite-backed. Sub-millisecond. Fully private.

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://python.org)
[![SQLite](https://img.shields.io/badge/SQLite-3.35+-green.svg)](https://sqlite.org/codeofethics.html)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Mnemosyne is a local-first memory system for the [Hermes Agent](https://github.com/AxDSan/hermes) framework. It stores conversations, preferences, and knowledge in SQLite with native vector search (sqlite-vec) and full-text search (FTS5) — no external databases, no API keys, no network calls.

---

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/AxDSan/mnemosyne.git
cd mnemosyne
pip install -e .

# 2. Register with Hermes
python -m mnemosyne.install

# 3. Activate as your memory provider
hermes memory setup
# → Select "mnemosyne" and press Enter
```

Or use the install script:

```bash
curl -sSL https://raw.githubusercontent.com/AxDSan/mnemosyne/main/deploy_hermes_provider.sh | bash
```

Verify:

```bash
hermes memory status       # Should show "Provider: mnemosyne"
hermes mnemosyne stats     # Shows working + episodic memory counts
```

> **Note:** The `hermes memory setup` picker defaults to "Built-in only" every time it opens. This is normal Hermes UI behavior — your previous selection **is** saved. Just select Mnemosyne and press Enter.

---

## What Makes It Different

| | Mnemosyne | Cloud alternatives |
|---|---|---|
| **Latency** | < 1ms | 10-100ms |
| **Dependencies** | Python stdlib + optional ONNX | External APIs, auth, rate limits |
| **Privacy** | 100% local | Data leaves your machine |
| **Cost** | Free | Freemium / per-call |
| **Setup** | `pip install -e .` | API keys, accounts, config |

**Key capabilities:**

- **BEAM architecture** — Three tiers: hot working memory, long-term episodic memory, temporary scratchpad
- **Hybrid search** — 50% vector similarity + 30% FTS5 rank + 20% importance, all inside SQLite
- **Automatic consolidation** — Old working memories are summarized and moved to episodic memory via `mnemosyne_sleep()`
- **Temporal triples** — Time-aware knowledge graph with automatic invalidation
- **Export / import** — Move your entire memory database to a new machine with one JSON file
- **Cross-session scope** — `remember(..., scope="global")` makes facts visible everywhere
- **Configurable compression** — `float32` (default), `int8` (4x smaller), or `bit` (32x smaller) vectors

---

## Benchmarks

All numbers measured on CPU with `sqlite-vec` + FTS5 enabled.

### LongMemEval (ICLR 2025)

| System | Score | Notes |
|---|---|---|
| **Mnemosyne (dense)** | **98.9% Recall@All@5** | Oracle subset, 100 instances, bge-small-en-v1.5 |
| Mempalace | 96.6% Recall@5 | AAAK + Palace architecture |
| Mastra Observational Memory | 84.23% (gpt-4o) | Three-date model |
| Full-context GPT-4o baseline | ~60.2% | No memory system |

### Latency vs. Cloud Alternatives

| Operation | Honcho | Zep | MemGPT | **Mnemosyne** | Speedup |
|---|---|---|---|---|---|
| **Write** | 45ms | 85ms | 120ms | **0.81ms** | **56x** |
| **Read** | 38ms | 62ms | 95ms | **0.076ms** | **500x** |
| **Search** | 52ms | 78ms | 140ms | **1.2ms** | **43x** |
| **Cold Start** | 500ms | 800ms | 1200ms | **0ms** | **Instant** |

### BEAM Architecture Scaling

**Write throughput:**

| Operation | Count | Total | Avg |
|---|---|---|---|
| Working memory writes | 500 | 8.7s | **17.4 ms** |
| Episodic inserts (with embedding) | 500 | 10.7s | **21.3 ms** |
| Sleep consolidation | 300 old items | 33 ms | — |

**Hybrid recall scaling (query latency stays flat as corpus grows):**

| Corpus Size | Query | Avg Latency | p95 |
|---|---|---|---|
| 100 | "concept 42" | **5.1 ms** | 6.9 ms |
| 500 | "concept 42" | **5.0 ms** | 5.7 ms |
| 1,000 | "concept 42" | **5.3 ms** | 6.5 ms |
| **2,000** | **"concept 42"** | **7.0 ms** | **8.6 ms** |

**Working memory recall scaling (FTS5 fast path):**

| WM Size | Query | Avg Latency | p95 |
|---|---|---|---|
| 1,000 | "concept 42" | **2.4 ms** | 3.1 ms |
| 5,000 | "domain 7" | **3.2 ms** | 3.8 ms |
| **10,000** | **"concept 42"** | **6.4 ms** | **7.2 ms** |

---

## Installation

### Prerequisites

- Python 3.9+
- Hermes Agent (for plugin integration)

### Basic

```bash
git clone https://github.com/AxDSan/mnemosyne.git
cd mnemosyne
pip install -e .
python -m mnemosyne.install
```

### Optional dependencies

```bash
# Dense retrieval (required for semantic search and the 98.9% LongMemEval score)
pip install fastembed>=0.3.0

# Local LLM consolidation (sleep cycle summarization)
pip install ctransformers>=0.2.27 huggingface-hub>=0.20
```

> **Note:** Without `fastembed`, Mnemosyne falls back to keyword-only retrieval. It still works, but you won't get competitive semantic search or the benchmark scores above.

### Uninstall

```bash
python -m mnemosyne.install --uninstall
```

---

## Usage

### CLI

```bash
# Show memory statistics
hermes mnemosyne stats

# Search memories
hermes mnemosyne inspect "dark mode preferences"

# Run consolidation (compress old working memory into episodic summaries)
hermes mnemosyne sleep

# Export all memories to a JSON file
hermes mnemosyne export --output mnemosyne_backup.json

# Import memories from a JSON file
hermes mnemosyne import --input mnemosyne_backup.json

# Clear scratchpad
hermes mnemosyne clear
```

### Python API

```python
from mnemosyne import remember, recall

# Store a fact
remember(
    content="User prefers dark mode interfaces",
    importance=0.9,
    source="preference"
)

# Store a global preference (visible in every session)
remember(
    content="User email is abdi.moya@gmail.com",
    importance=0.95,
    source="preference",
    scope="global"
)

# Store a temporary credential with expiry
remember(
    content="API key: sk-abc123",
    importance=0.8,
    source="credential",
    valid_until="2026-12-31T00:00:00"
)

# Search memories
results = recall("interface preferences", top_k=3)

# Temporal knowledge graph
from mnemosyne.core.triples import TripleStore
kg = TripleStore()
kg.add("Maya", "assigned_to", "auth-migration", valid_from="2026-01-15")
kg.query("Maya", as_of="2026-02-01")
```

### Advanced: BEAM direct access

```python
from mnemosyne.core.beam import BeamMemory

beam = BeamMemory(session_id="my_session")

# Working memory (auto-injected into prompts)
beam.remember("Important context", importance=0.9)

# Episodic memory (long-term, searchable)
beam.consolidate_to_episodic(
    summary="User likes Neovim",
    source_wm_ids=["wm1"],
    importance=0.8
)

# Scratchpad (temporary reasoning)
beam.scratchpad_write("todo: fix auth bug")

# Search both tiers
results = beam.recall("editor preferences", top_k=5)
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    HERMES AGENT                              │
│                                                              │
│  ┌─────────────┐     ┌──────────────┐     ┌─────────────┐  │
│  │   pre_llm   │────▶│  Mnemosyne   │────▶│   SQLite    │  │
│  │    hook     │     │    BEAM      │     │             │  │
│  └─────────────┘     └──────────────┘     │ working_mem │  │
│         ▲                                  │ episodic_mem│  │
│         │                                  │ vec_episodes│  │
│         └──────── Auto-injected context ───│ fts_episodes│  │
│                                            │ scratchpad  │  │
│                                            │ triples     │  │
│                                            └─────────────┘  │
│                                                              │
│  No HTTP. No cloud. 100% local.                              │
└─────────────────────────────────────────────────────────────┘
```

**BEAM** (Bilevel Episodic-Associative Memory):

- `working_memory` — Hot context, auto-injected before LLM calls, TTL-based eviction
- `episodic_memory` — Long-term storage with sqlite-vec + FTS5 hybrid search
- `scratchpad` — Temporary agent reasoning workspace

---

## Why SQLite for Hermes?

SQLite is already in your stack. Hermes uses it for session persistence. Mnemosyne extends that same file — no new dependencies, no Docker containers, no connection pooling.

| Feature | Honcho | Zep | Mnemosyne |
|---|---|---|---|
| Deployment | Docker + PostgreSQL | Docker + Postgres | `pip install` |
| Query Language | REST API | REST API | `SELECT ... WHERE MATCH` |
| Vector Store | pgvector | pgvector | sqlite-vec |
| Text Search | Separate API | Separate API | Built-in FTS5 |
| Auth Required | Yes (supabase) | Yes | No |
| Offline Mode | No | No | Yes |
| Cold Start Latency | 500-800ms | 800ms+ | **0ms** |

---

## Backup, Export & Migration

Mnemosyne stores everything in a single SQLite file at `~/.hermes/mnemosyne/data/mnemosyne.db`.

```bash
# Simple backup
cp ~/.hermes/mnemosyne/data/mnemosyne.db ~/backups/mnemosyne_$(date +%Y%m%d).db

# Export to JSON (portable across machines)
hermes mnemosyne export --output mnemosyne_backup.json

# Import on a new machine
hermes mnemosyne import --input mnemosyne_backup.json
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MNEMOSYNE_DATA_DIR` | `~/.hermes/mnemosyne/data` | Database directory |
| `MNEMOSYNE_VEC_TYPE` | `float32` | Vector compression: `float32`, `int8`, or `bit` |
| `MNEMOSYNE_WM_MAX_ITEMS` | `10000` | Working memory item limit |
| `MNEMOSYNE_WM_TTL_HOURS` | `24` | Working memory TTL |
| `MNEMOSYNE_RECENCY_HALFLIFE` | `168` | Recency decay halflife in hours (1 week) |

---

## Testing

```bash
# Run tests
python -m pytest tests/test_beam.py -v

# Run benchmarks
python tests/benchmark_beam_working_memory.py
```

---

## Contributing

Contributions are welcome. Areas of active interest:

- [ ] Encrypted cloud sync (optional, user-controlled)
- [ ] Browser extension for web context capture
- [ ] Additional embedding models
- [ ] Multi-language support

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## License

MIT License — See [LICENSE](LICENSE)

Copyright (c) 2026 Abdias J

---

## Acknowledgments

- [Hermes Agent Framework](https://github.com/AxDSan/hermes) — The ecosystem Mnemosyne was built for
- [Honcho](https://github.com/plasticlabs/honcho) — For defining the stateful memory space
- [Mempalace](https://github.com/thepersonalaicompany/mempalace) — For proving local-first memory can compete on benchmarks
- [SQLite](https://sqlite.org/codeofethics.html) — The world's most deployed database

---

<p align="center">
  <em>"The faintest ink is more powerful than the strongest memory." — Hermes Trismegistus</em>
</p>
