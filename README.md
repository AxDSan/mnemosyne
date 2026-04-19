# Mnemosyne

> **The Native Memory System for Hermes Agent**  
> Zero-cloud. Sub-millisecond latency. Complete privacy. Now with dense retrieval.

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://python.org)
[![SQLite](https://img.shields.io/badge/SQLite-3.35+-green.svg)](https://sqlite.org/codeofethics.html)
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

### 8. FTS5 for Working Memory (No More Python Loops)
`recall()` used to fetch every row in `working_memory` into Python and score keywords in a loop. We added a native `fts_working` virtual table with SQLite triggers. Now working-memory recall runs inside SQLite, just like episodic memory. **100x speedup at scale**.

### 9. Query Embedding LRU Cache
Repeated queries are extremely common in agent loops. We added `@lru_cache(maxsize=512)` to query embeddings. The second time you ask the same thing, the embedding is **instant**.

### 10. Configurable Vector Compression (`int8` / `bit`)
Episodic vectors are now configurable via `MNEMOSYNE_VEC_TYPE`:
- `float32` — default, maximum accuracy
- `int8` — **4x smaller** (~384 bytes/vector), high accuracy
- `bit` — **32x smaller** (~48 bytes/vector), massive scale

With `bit`, you can store **millions of episodic memories** on a 4GB RAM machine. sqlite-vec handles quantization natively via `vec_quantize_binary()` and `vec_quantize_int8()`.

### 11. Batch Ingestion & Robust Plugin
- `remember_batch()` for high-throughput working-memory writes (5,000 items in ~0.3s).
- The Hermes plugin now imports `mnemosyne` robustly whether it is installed via pip, cloned, or loaded as a skill. No more `sys.path` guesswork in production.

### 12. Recall Tracking & Behavioral Scoring (v2)
Every memory now tracks `recall_count` and `last_recalled`. Frequently accessed memories surface higher; memories that have been recalled too many times recently are naturally deprioritized (saturation avoidance). Recency decay scoring (`base_score * (0.7 + 0.3 * decay)`) makes recent context float up unless high-importance memories override it.

### 13. Exact-Match Deduplication
Storing the same credential three times is now impossible. `remember()` checks for exact content matches within the session and updates the existing row (bumping importance and timestamp) instead of creating a duplicate.

### 14. Local LLM Consolidation (v2)
The sleep cycle now uses a **local TinyLlama-1.1B-Chat** model (~640MB) for actual semantic summarization instead of lossy AAAK compression. Falls back to AAAK encoding if the model is unavailable. Zero cloud API calls. Fully offline after initial download.

### 15. Temporal Validity & Invalidation (v2)
Memories have a shelf life. Set `valid_until="2026-12-31"` and the memory auto-expires — `recall()` filters it out. Use `mnemosyne_invalidate` to mark facts as superseded by newer knowledge (e.g., "User moved from Miami to Austin").

### 16. Cross-Session Global Memory (v2)
User preferences should travel everywhere. `remember(..., scope="global")` makes a memory visible in **every** Hermes session. `recall()` searches both the current session and global memories. `get_context()` prioritizes globals first so preferences always surface.

---

## 🔄 Upgrading from Earlier Mnemosyne Versions

If you started with Mnemosyne before the BEAM architecture (pre-April 2026), run the migration script:

```bash
cd ~/.hermes/plugins/mnemosyne
python scripts/migrate_from_legacy.py
```

### ⚠️ CRITICAL for Fly.io / Ephemeral VMs

On Fly.io, **only `~/.hermes` is persisted** across restarts. Mnemosyne now defaults to:

```
~/.hermes/mnemosyne/data/mnemosyne.db  (persisted ✅)
```

NOT `~/.mnemosyne/data/` (ephemeral — data lost on restart ❌).

Set this explicitly if needed:
```bash
export MNEMOSYNE_DATA_DIR="/root/.hermes/mnemosyne/data"
```

```bash
cd ~/.hermes/hermes-agent/plugins/memory/mnemosyne  # or wherever you cloned the repo
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

### Working Memory Recall Scaling (NEW)

The real bottleneck was working memory. With the new FTS5 fast path, recall stays flat even with 10,000 hot memories:

| WM Size | Query | Avg Latency | p95 |
|---------|-------|-------------|-----|
| 1,000 | "concept 42" | **2.4 ms** | 3.1 ms |
| 5,000 | "domain 7" | **3.2 ms** | 3.8 ms |
| **10,000** | **"concept 42"** | **6.4 ms** | 7.2 ms |

At 10,000 working memories, recall is still **sub-10ms**. The old architecture would degrade to **seconds** because it looped over every row in Python.

**Why this matters for Hermes:** Memory retrieval stays invisible. Even as your agent accumulates months of conversation, context injection never becomes a bottleneck.

---

## 🚀 Hermes Plugin Integration

### Zero-Config Auto-Context

Mnemosyne registers hooks with Hermes for seamless operation. The plugin uses Hermes's `pre_llm_call` hook (plugin-level, no core changes needed) to inject relevant context before every LLM call:

```python
# Mnemosyne automatically injects this before EVERY LLM call:

════════════════════════════════════════════════════════════════
MNEMOSYNE MEMORY (persistent local context)
Use this to answer questions about the user and prior work.

[2026-04-05 10:23] PREF|Neovim>Vim
[2026-04-05 09:15] PROJ|FluxSpeak AI
[2026-04-05 08:42] LOC|America/New_York
════════════════════════════════════════════════════════════════
```

Global memories are injected first, followed by session-specific context. Expired and superseded memories are automatically filtered out.

### Hermes Tools Provided

| Tool | Purpose |
|------|---------|
| `mnemosyne_remember` | Store facts, preferences, context (supports `valid_until`, `scope`) |
| `mnemosyne_recall` | Search stored memories (hybrid vec + FTS5 + recency decay) |
| `mnemosyne_invalidate` | Mark a memory as expired or superseded |
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
# Install directly via Hermes's built-in plugin manager
hermes plugins install AxDSan/mnemosyne

# Restart Hermes to load the plugin
hermes gateway restart
```

**Or install from a local clone (for development):**
```bash
git clone https://github.com/AxDSan/mnemosyne.git
cd mnemosyne
hermes plugins install . --force
hermes gateway restart
```

**Updating the plugin:**
```bash
hermes plugins install AxDSan/mnemosyne --force
hermes gateway restart
```

Without `fastembed`, Mnemosyne falls back to keyword-only retrieval (functional but not competitive on semantic benchmarks).

### Optional: Auto-Log Tool Calls

By default, Mnemosyne **does not** automatically save every `terminal`, `execute_code`, or `write_file` call to memory. If you want to opt into that behavior, set:

```bash
export MNEMOSYNE_LOG_TOOLS=1
```

We disable this by default because it rapidly floods working memory with operational noise, making recall and context injection less useful.

### Vector Compression (Optional but Powerful)

Control how much RAM your episodic vectors consume:

```bash
# Maximum compression — millions of memories on a small VPS
export MNEMOSYNE_VEC_TYPE=bit

# Great balance — 4x smaller with minimal accuracy loss
export MNEMOSYNE_VEC_TYPE=int8

# Default — maximum precision
export MNEMOSYNE_VEC_TYPE=float32
```

### Option 2: pip (Standalone Use)

```bash
pip install mnemosyne-memory
```

For local LLM consolidation (optional):
```bash
pip install mnemosyne-memory[llm]
# or manually:
pip install ctransformers huggingface-hub
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

# Store a global preference that survives across sessions
remember(
    content="User email is abdi.moya@gmail.com",
    importance=0.95,
    source="preference",
    scope="global"  # visible in every session
)

# Store a temporary fact with expiry
remember(
    content="Cartesia API key: sk-abc123",
    importance=0.8,
    source="credential",
    valid_until="2026-12-31T00:00:00"  # auto-expires
)

# Recall with semantic relevance (uses dense embeddings if available)
results = recall("interface preferences", top_k=3)

# Invalidate outdated knowledge
from mnemosyne.core.beam import BeamMemory
beam = BeamMemory()
beam.invalidate(old_memory_id, replacement_id=new_memory_id)

# Temporal knowledge graph
from mnemosyne.core.triples import TripleStore
kg = TripleStore()
kg.add("Maya", "assigned_to", "auth-migration", valid_from="2026-01-15")
kg.query("Maya", as_of="2026-02-01")
```

---

## 💻 CLI Commands

Mnemosyne is primarily a library. Use Python directly:

```bash
# Quick stats check
python3 -c "from mnemosyne import get_stats; print(get_stats())"

# Run tests
python -m pytest tests/test_beam.py -v

# Run benchmarks
python tests/benchmark_beam_working_memory.py
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
- **Shared values** — Guided by the same [Code of Ethics](https://sqlite.org/codeofethics.html) that shapes SQLite

---

## 🛡️ Disaster Recovery

Mnemosyne stores everything in a single SQLite file. Back it up like any database:

```bash
# Simple backup
cp ~/.hermes/mnemosyne/data/mnemosyne.db ~/backups/mnemosyne_$(date +%Y%m%d).db

# Compressed backup
gzip -c ~/.hermes/mnemosyne/data/mnemosyne.db > ~/backups/mnemosyne_$(date +%Y%m%d).db.gz

# Restore
cp ~/backups/mnemosyne_20260405.db ~/.hermes/mnemosyne/data/mnemosyne.db
```

**Database location:** `~/.hermes/mnemosyne/data/mnemosyne.db`

**Legacy data (pre-BEAM):** `~/.hermes/mnemosyne/data/mnemosyne_native.db`

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
| **Context Compression** | ❌ No | ✅ AAAK dialect | **✅ AAAK + local LLM summarization** |
| **Recall Tracking** | ❌ No | ❌ No | **✅ Yes (recall_count, last_recalled)** |
| **Recency Decay** | ❌ No | ❌ No | **✅ Yes (time-weighted scoring)** |
| **Temporal Validity** | ❌ No | ❌ No | **✅ Yes (valid_until, invalidation)** |
| **Global Scope** | ❌ No | ❌ No | **✅ Yes (cross-session memories)** |
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

# Install in Hermes
hermes plugins install /path/to/mnemosyne
hermes plugins enable mnemosyne
hermes gateway restart

# Verify plugin loaded
hermes plugins list

# Run benchmarks
python tests/benchmark_beam_working_memory.py
```

---

## 🤝 Contributing

Mnemosyne is the default memory system for Hermes. Contributions welcome:

- [x] BEAM architecture (working + episodic + scratchpad)
- [x] Native vector search with `sqlite-vec`
- [x] FTS5 full-text hybrid retrieval (episodic + working memory)
- [x] Sleep / consolidation cycle
- [x] Dense retrieval with fastembed
- [x] Temporal triples
- [x] AAAK-style compression
- [x] Query embedding LRU cache
- [x] Configurable vector compression (`int8` / `bit`)
- [x] Batch ingestion (`remember_batch`)
- [x] Recall tracking + recency decay scoring
- [x] Exact-match deduplication on write
- [x] Local LLM consolidation (TinyLlama)
- [x] Temporal validity + invalidation
- [x] Cross-session global memory
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
- **[SQLite](https://sqlite.org/codeofethics.html)** — The world's most deployed database

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
