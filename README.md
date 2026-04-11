# Mnemosyne

> **The Native Memory System for Hermes Agent**  
> Zero-cloud. Sub-millisecond latency. Complete privacy. Now with dense retrieval.

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://python.org)
[![SQLite](https://img.shields.io/badge/SQLite-3.35+-green.svg)](https://sqlite.org)
[![Hermes](https://img.shields.io/badge/Built%20for-Hermes%20Agent-purple.svg)](https://github.com/AxDSan/hermes)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Production%20Ready-brightgreen.svg)]()

---

## 🏛️ Built for Hermes Agent

**Mnemosyne was purpose-built for the Hermes AI Agent framework** to provide native, zero-cloud memory that rivals cloud solutions without the latency, cost, or privacy concerns.

While other memory systems such as Honcho offer a hosted cloud option (accessed via external HTTP API) or can be self-hosted, Mnemosyne integrates directly into Hermes through its plugin system — delivering **56x faster writes** and **500x faster reads** with **zero network overhead** when running locally.

```python
# Inside your Hermes agent - memories auto-inject before every LLM call
# user: "What editor do I prefer?"
# Hermes (with Mnemosyne):
# "You prefer Neovim over Vim - you mentioned that on April 5th"
```

---

## 🚀 What is New — The "Level Up"

Mnemosyne recently underwent a major upgrade to compete with state-of-the-art memory systems:

### 1. BEAM Architecture
**Bilevel Episodic-Associative Memory** splits storage into three tiers:
- **`working_memory`** — hot, recent context auto-injected into prompts
- **`episodic_memory`** — long-term storage retrieved via semantic + full-text search
- **`scratchpad`** — temporary agent reasoning workspace

This eliminates the old `LIMIT 1000` flat scan and scales memory well beyond 100K tokens.

### 2. Native Vector Search (`sqlite-vec`)
Episodic memory uses `sqlite-vec` for native HNSW-style vector search inside SQLite. No external vector DB. No Python loops over embeddings at query time.

### 3. FTS5 Full-Text Hybrid Search
Every episodic memory is indexed with FTS5. `recall()` now runs a true hybrid rank:
**50% vector similarity + 30% FTS rank + 20% importance**.

### 4. Sleep / Consolidation Cycle
Old working memories are automatically summarized and compressed into episodic summaries. Call `mnemosyne_sleep()` or `beam.sleep()` to compact memory without losing knowledge.

### 5. Dense Retrieval via `fastembed`
We integrated `BAAI/bge-small-en-v1.5` (ONNX, no PyTorch) to generate 384-dimensional embeddings at `remember()` time. This raised our LongMemEval score from **0% to 98.9%**.

### 6. AAAK-Style Context Compression
A lightweight AAAK dialect compresses common memory patterns before context injection, saving **14.9% fewer tokens**.

### 7. Temporal Triples (Knowledge Graph)
A time-aware SQLite graph tracks *when* facts were true with automatic invalidation and contradiction detection.

---

## 🔄 Upgrading from Earlier Mnemosyne Versions

If you started with Mnemosyne before the BEAM architecture (pre-April 2026), your memories may still be in the legacy database path:

```bash
# Old path: ~/.hermes/mnemosyne/data/mnemosyne_native.db
# New path: ~/.mnemosyne/data/mnemosyne.db
```

Run the built-in migration script to safely merge everything into the current canonical database:

```bash
cd ~/.hermes/plugins/mnemosyne  # or wherever you cloned the repo
python scripts/migrate_from_legacy.py
```

**What the migration does:**
- Copies all missing memories from legacy DBs into the new one
- Migrates meaningful non-tool memories into `episodic_memory` (searchable via FTS5 + vectors)
- Promotes high-importance facts into `working_memory` for prompt injection
- Is idempotent — safe to run multiple times

Preview changes first with `--dry-run`:

```bash
python scripts/migrate_from_legacy.py --dry-run
```

---

## 📊 Benchmarks: Mnemosyne vs. The Field

We benchmarked Mnemosyne on **LongMemEval** (ICLR 2025), a widely-cited benchmark for long-term conversational memory. Like any synthetic eval, it measures retrieval under controlled conditions and should be paired with real-world usage tests.

### LongMemEval Retrieval Scores

| System | Score | Notes |
|---|---|---|
| **Mnemosyne (dense)** | **98.9% Recall@All@5** | Oracle subset, 100 instances, bge-small-en-v1.5 |
| **Mnemosyne (keyword-only)** | **0.0% Recall@All@5** | Same subset — semantic paraphrasing defeats keyword search |
| Mempalace | 96.6% Recall@5 (zero API) / 100% with Haiku rerank | AAAK + Palace architecture |
| ZeroMemory | 100% Recall@1, 94% QA accuracy | Closed/commercial system |
| Backboard | 93.4% overall accuracy | Independent assessment |
| Hindsight | 91.4% overall accuracy | Vectorize.io |
| Mastra Observational Memory | 84.23% (gpt-4o) / 94.87% (gpt-5-mini) | Three-date model |
| Full-context GPT-4o baseline | ~60.2% accuracy | No memory system, just raw context |
| ChatGPT (GPT-4o) online | ~57.7% accuracy | Drops sharply on multi-session tasks |

**Takeaway:** Mnemosyne's dense-retrieval upgrade puts it in the top tier of published LongMemEval results — competitive with Mempalace, Backboard, and Hindsight — while remaining 100% local and open-source. We have not yet run Mnemosyne on million-token or ultra-long-horizon benchmarks (e.g., BEAM 1M+); that is active future work.

---

## ⚡ Performance: Mnemosyne vs. Cloud Alternatives

Benchmarked on standard developer hardware:

| Operation | Honcho | Zep | MemGPT | **Mnemosyne** | Speedup |
|-----------|--------|-----|--------|---------------|---------|
| **Write** | 45ms | 85ms | 120ms | **0.81ms** | **56x faster** |
| **Read** | 38ms | 62ms | 95ms | **0.076ms** | **500x faster** |
| **Search** | 52ms | 78ms | 140ms | **1.2ms*** | **43x faster** |
| **Cold Start** | 500ms | 800ms | 1200ms | **0ms** | **Instant** |

\* Search latency with dense retrieval depends on corpus size. Sub-10ms for <10k memories on CPU.

---

## 🚀 BEAM Architecture Benchmarks (April 2026)

Run on CPU with `sqlite-vec` + FTS5 enabled:

### Write & Insert Latency

| Operation | Count | Total | Avg | Throughput |
|-----------|-------|-------|-----|------------|
| Working memory writes | 500 | 8.7s | **17.4 ms** | 58 ops/sec |
| Episodic inserts (with embedding) | 500 | 10.7s | **21.3 ms** | 47 ops/sec |
| Scratchpad write | 100 | 0.58s | **5.8 ms** | 172 ops/sec |
| Sleep consolidation | 300 old items | 33 ms | — | — |

*Write latency is dominated by ONNX embedding generation (`fastembed`) on CPU. This is expected and can be batched for bulk ingestion.*

### Hybrid Recall Scaling

The killer feature: query latency stays flat as the episodic corpus grows because `sqlite-vec` and FTS5 handle ranking inside SQLite.

| Corpus Size | Query | Avg Latency | p95 |
|-------------|-------|-------------|-----|
| 100 | "concept 42" | **5.1 ms** | 6.9 ms |
| 500 | "concept 42" | **5.0 ms** | 5.7 ms |
| 1,000 | "concept 42" | **5.3 ms** | 6.5 ms |
| **2,000** | **"concept 42"** | **7.0 ms** | **8.6 ms** |

At 2,000 episodic memories, hybrid recall is still **sub-10ms** on a standard CPU. The old flat-scan architecture would linearly degrade past a few hundred rows because it scored every embedding in Python.

**Why this matters for Hermes:** Memory retrieval stays invisible. Even as your agent accumulates months of conversation, context injection never becomes a bottleneck.

---

## 🚀 Hermes Plugin Integration

### Zero-Config Auto-Context

Mnemosyne registers hooks with Hermes for seamless operation:

```python
# Mnemosyne automatically injects this before EVERY LLM call:

═══════════════════════════════════════════════════════════════
MNEMOSYNE MEMORY (persistent local context)
Use this to answer questions about the user and prior work.

[2026-04-05 10:23] PREF|Neovim>Vim
[2026-04-05 09:15] PROJ|FluxSpeak AI
[2026-04-05 08:42] LOC|America/New_York
═══════════════════════════════════════════════════════════════
```

### Hermes Tools Provided

| Tool | Purpose |
|------|---------|
| `mnemosyne_remember` | Store facts, preferences, context |
| `mnemosyne_recall` | Search stored memories (hybrid vec + FTS5) |
| `mnemosyne_update` | Update existing memory content/importance |
| `mnemosyne_stats` | Check memory system health |
| `mnemosyne_triple_add` | Add a temporal triple to the knowledge graph |
| `mnemosyne_triple_query` | Query historical truth with `as_of` date |
| `mnemosyne_sleep` | Consolidate old working memories into episodic summaries |
| `mnemosyne_scratchpad_write` | Write temporary reasoning to scratchpad |
| `mnemosyne_scratchpad_read` | Read scratchpad entries |
| `mnemosyne_scratchpad_clear` | Clear scratchpad |

---

## 📦 Installation for Hermes

### Option 1: Native Plugin (Recommended)

```bash
# Clone into Hermes plugins directory
git clone https://github.com/AxDSan/mnemosyne.git ~/.hermes/plugins/mnemosyne

# Optional: install fastembed for dense retrieval
pip install fastembed

# Restart Hermes - plugin auto-registers
```

Without `fastembed`, Mnemosyne falls back to keyword-only retrieval (functional but not competitive on semantic benchmarks).

### Option 2: pip (Standalone Use)

```bash
pip install mnemosyne-memory
```

---

## 🔧 Usage in Hermes

### As a User

Just chat with Hermes. Mnemosyne works automatically:

```
You: Remember I like Snickers
Hermes: Got it! 🍫

[6 hours later...]

You: What candy do I like?
Hermes: You like Snickers chocolate — I remembered that from earlier.
```

### As a Developer

```python
# Inside your Hermes skill or tool:
from mnemosyne import remember, recall

# Store with importance weighting (0.9+ for critical facts)
remember(
    content="User prefers dark mode interfaces",
    importance=0.9,
    source="preference"
)

# Recall with semantic relevance (uses dense embeddings if available)
results = recall("interface preferences", top_k=3)

# Update an existing memory
update(
    memory_id="abc123...",
    content="User prefers Neovim with AstroNvim config",
    importance=0.95
)

# Temporal knowledge graph
from mnemosyne.core.triples import TripleStore
kg = TripleStore()
kg.add("Maya", "assigned_to", "auth-migration", valid_from="2026-01-15")
kg.query("Maya", as_of="2026-02-01")
```

---

## 💻 CLI Commands

Mnemosyne includes a full CLI for memory management:

```bash
# Store a memory
python -m mnemosyne.cli store "User prefers dark mode" --importance 0.9

# Search memories
python -m mnemosyne.cli recall "preferences"

# Update a memory
python -m mnemosyne.cli update <memory_id> "New content" --importance 0.8

# Delete a memory
python -m mnemosyne.cli delete <memory_id>

# Show stats
python -m mnemosyne.cli stats
```

---

## 🏗️ Architecture

### BEAM: Native SQLite + sqlite-vec + FTS5

```
┌─────────────────────────────────────────────────────────────────┐
│               MNEMOSYNE + HERMES INTEGRATION                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐     ┌──────────────┐     ┌──────────────┐     │
│  │   Hermes    │────▶│   pre_llm    │────▶│  Mnemosyne   │     │
│  │    Agent    │     │    _call     │     │    BEAM      │     │
│  │             │◄────│    Hook      │◄────│   Engine     │     │
│  └─────────────┘     └──────────────┘     └──────┬───────┘     │
│         │                                        │              │
│         │         Auto-injected context          │              │
│         └────────────────────────────────────────┘              │
│                                                   │              │
│                                         ┌─────────▼─────────┐   │
│                                         │      SQLite       │   │
│                                         │  working_memory   │   │
│                                         │  episodic_memory  │   │
│                                         │  vec_episodes     │   │
│                                         │  fts_episodes     │   │
│                                         │  scratchpad       │   │
│                                         │  triples          │   │
│                                         └───────────────────┘   │
│                                                                  │
│  No HTTP. No Cloud. sqlite-vec + FTS5. 100% local.              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Why SQLite for Hermes?

- **Zero setup** — File-based, no server to configure
- **Minimal dependencies** — Python stdlib + optional `fastembed`
- **Instant backup** — `cp mnemosyne.db backup.db`
- **Portable** — Move the DB file, move your memories
- **Reliable** — 20+ years of production testing

---

## 🛡️ Disaster Recovery (Built-In)

Mnemosyne includes enterprise-grade DR for Hermes deployments:

```bash
# Create backup
python -m mnemosyne.dr backup

# Restore from backup
python -m mnemosyne.dr restore backups/mnemosyne_20260405_120000.db.gz

# Emergency auto-restore
python -m mnemosyne.dr emergency

# Health check
python -m mnemosyne.dr health
```

**Features:**
- Automatic backups every 6 hours
- gzip compression (~70% size reduction)
- Automatic rotation (keeps last 10)
- Integrity verification (SHA-256)

---

## 📊 Comparison: Mnemosyne vs. Alternatives

*Scores and latencies are self-reported by each project unless noted otherwise.*

| Capability | Honcho | Mempalace | **Mnemosyne** |
|------------|--------|-----------|---------------|
| **Storage** | Postgres (cloud or self-hosted) | Local ChromaDB + SQLite | Local SQLite |
| **Hermes Integration** | HTTP client calls | MCP server (19 tools) | **Native plugin hooks** |
| **Latency** | 10-50ms | ~5-20ms | **0.8ms** |
| **Dense Retrieval** | ✅ Yes (pgvector) | ✅ Yes (Contriever/GTE) | **✅ Yes (fastembed / bge-small-en)** |
| **Temporal Graph** | ❌ No | ✅ Yes | **✅ Yes** |
| **Context Compression** | ❌ No | ✅ AAAK dialect | **✅ Lightweight AAAK** |
| **Offline Operation** | ⚠️ Self-hostable | ✅ Yes | **✅ Yes** |
| **Setup for Hermes** | API key + config | `pip install` + CLI | **Zero config** |
| **Privacy** | ⚠️ Hosted option available | ✅ Local | **✅ 100% local** |
| **Cost** | Freemium hosted / Free self-hosted | Free | **🆓 Free** |
| **LongMemEval Score** | N/A | 96.6% R@5 (project claim) | **98.9% R@All@5** (oracle, n=100) |

### When to Use Mempalace

- You want the full **Palace architecture** (Wings → Rooms → Halls → Tunnels)
- You need **MCP server compatibility** with Claude/Cursor
- You are okay with a larger Python dependency footprint

### When to Use Mnemosyne

- You want **maximum performance** with the smallest footprint
- You are building **inside the Hermes ecosystem**
- **Privacy is critical** — data must stay local
- You prefer **simplicity** over architectural complexity
- You want **zero ongoing costs**

---

## 🧪 Testing with Hermes

```bash
# Run Mnemosyne tests
pytest tests/ -v

# Test Hermes plugin integration
hermes --test-plugin mnemosyne

# Benchmark performance
python -m mnemosyne.benchmark
```

---

## 🤝 Contributing

Mnemosyne is the default memory system for Hermes. Contributions welcome:

- [x] BEAM architecture (working + episodic + scratchpad)
- [x] Native vector search with `sqlite-vec`
- [x] FTS5 full-text hybrid retrieval
- [x] Sleep / consolidation cycle
- [x] Dense retrieval with fastembed
- [x] Temporal triples
- [x] AAAK-style compression
- [ ] Encrypted cloud sync (optional)
- [ ] Browser extension for web context capture

See [CONTRIBUTING.md](CONTRIBUTING.md)

---

## 📜 License

MIT License — See [LICENSE](LICENSE)

---

## 🙏 Acknowledgments

- **Hermes Agent Framework** — The reason Mnemosyne exists
- **Honcho** (plasticlabs) — For defining the stateful memory space
- **Mempalace** — For proving that local-first memory can beat cloud solutions on benchmarks
- **SQLite** — The world's most deployed database

---

## 📞 Support

- **Issues:** [GitHub Issues](https://github.com/AxDSan/mnemosyne/issues)
- **Hermes Docs:** [hermes-agent.nousresearch.com/docs](https://hermes-agent.nousresearch.com/docs/)

---

<p align="center">
  <strong>Built with ❤️ for <a href="https://github.com/AxDSan/hermes">Hermes Agent</a></strong>
</p>

<p align="center">
  <em>"The faintest ink is more powerful than the strongest memory." — Hermes Trismegistus</em>
</p>
