# API Reference — Mnemosyne v2.1

## Quick Start

```python
from mnemosyne import Mnemosyne

# Initialize (uses ~/.hermes/mnemosyne/data/ by default)
mem = Mnemosyne()

# Store memories
mem.remember("User prefers dark mode", importance=0.9)

# Recall memories
results = mem.recall("user preferences", top_k=5)

# Get stats
stats = mem.get_stats()
```

---

## Module-Level Convenience Functions

```python
from mnemosyne import remember, recall, get_stats, forget, update, get_context
```

These functions create a default `Mnemosyne` instance and delegate to it.

| Function | Signature | Description |
|---|---|---|
| `remember()` | `(content, source="conversation", importance=0.5, **kwargs) -> str` | Store a memory, returns memory ID |
| `recall()` | `(query, top_k=5, **kwargs) -> list` | Search memories |
| `get_stats()` | `() -> dict` | Memory statistics |
| `forget()` | `(memory_id) -> bool` | Delete a memory |
| `update()` | `(memory_id, **kwargs) -> bool` | Update a memory |
| `get_context()` | `(query, top_k=5) -> str` | Get formatted context string |

---

## Mnemosyne Class

**Module:** `mnemosyne.core.memory`

```python
from mnemosyne.core.memory import Mnemosyne

# Default instance
mem = Mnemosyne()

# With custom data directory
mem = Mnemosyne(data_dir="/path/to/data")

# With memory bank
mem = Mnemosyne(bank="work")

# With session ID
mem = Mnemosyne(session_id="my-agent-session")
```

### Constructor

```python
Mnemosyne(
    data_dir: str = None,        # Custom data directory (default: ~/.hermes/mnemosyne/data/)
    session_id: str = None,      # Session identifier (default: auto-generated UUID)
    bank: str = None             # Memory bank name for isolation
)
```

### `remember()`

Store a memory. Returns the memory ID.

```python
memory_id = mem.remember(
    content: str,                      # The text to remember
    source: str = "conversation",      # Origin: "conversation", "document", "system"
    importance: float = 0.5,           # 0.0–1.0 relevance score
    metadata: dict = None,             # Optional additional fields
    valid_until: str = None,           # ISO timestamp when this memory expires
    scope: str = "session",            # "session" or "global"
    extract_entities: bool = False,    # Extract entity mentions as triples
    extract: bool = False              # Extract structured facts via LLM
)
```

**Examples:**

```python
# Basic storage
mid = mem.remember("User prefers dark mode", importance=0.9)

# With entity extraction
mid = mem.remember("Abdias founded Mnemosyne in New York",
                    extract_entities=True)

# With fact extraction (requires LLM)
mid = mem.remember("The project deadline is June 15th",
                    extract=True, importance=0.8)

# Session-scoped (auto-evicted after TTL)
mid = mem.remember("Current task: fixing bug #42",
                    scope="session", source="system")

# Global (persists across sessions)
mid = mem.remember("User's timezone is EST",
                    scope="global")
```

### `recall()`

Search memories using hybrid vector + FTS + importance scoring.

```python
results = mem.recall(
    query: str,                           # Search query
    top_k: int = 5,                       # Number of results
    source: str = None,                   # Filter by source
    threshold: float = 0.0,               # Minimum score threshold
    scope: str = None,                    # Filter by scope
    temporal_weight: float = 0.0,         # 0.0–1.0, boosts recent memories
    query_time = None,                    # datetime or ISO string for temporal calculation
    temporal_halflife: float = None,      # Hours for decay (default: 24)
    vec_weight: float = None,             # Override vector scoring weight
    fts_weight: float = None,             # Override FTS scoring weight
    importance_weight: float = None       # Override importance weight
)
```

Returns a list of dicts with keys: `id`, `content`, `score`, `source`, `timestamp`, `importance`, `scope`, `metadata`.

**Examples:**

```python
# Basic recall
results = mem.recall("user preferences")

# Temporal boost — prefer recent memories
results = mem.recall("current task", temporal_weight=0.5)

# Custom scoring weights
results = mem.recall("python code",
    vec_weight=0.7, fts_weight=0.2, importance_weight=0.1)

# Filter by source
results = mem.recall("meeting notes", source="document")
```

### `update()`

Update an existing memory.

```python
success = mem.update(
    memory_id: str,
    content: str = None,
    importance: float = None,
    metadata: dict = None
)
```

### `forget()`

Delete a memory by ID.

```python
success = mem.forget(memory_id: str)
```

### `sleep()`

Run BEAM consolidation cycle — moves working memory to episodic storage.

```python
stats = mem.sleep()
# Returns dict with consolidation statistics
```

### `get_stats()`

Get memory statistics.

```python
stats = mem.get_stats()
# Returns dict with counts per tier, session info, etc.
```

### `get_context()`

Get a formatted context string for LLM injection.

```python
context = mem.get_context(query="recent tasks", top_k=5)
# Returns formatted string ready for prompt injection
```

### `export_to_file()` / `import_from_file()`

Portable export/import for backup and migration.

```python
mem.export_to_file("backup.json")
mem.import_from_file("backup.json")
```

### v2 Properties (lazy-initialized)

```python
mem.stream           # MemoryStream — event stream
mem.compressor       # MemoryCompressor — compress/decompress
mem.patterns         # PatternDetector — temporal/content patterns
mem.delta_sync       # DeltaSync — incremental sync
mem.plugin_manager   # PluginManager — plugin lifecycle
```

---

## BeamMemory Class

**Module:** `mnemosyne.core.beam`

The BEAM (Bilevel Episodic-Associative Memory) engine. Usually accessed through `Mnemosyne`, but can be used directly.

```python
from mnemosyne.core.beam import BeamMemory

beam = BeamMemory(
    session_id: str = "default",
    db_path: Path = None
)
```

### Key Methods

| Method | Description |
|---|---|
| `remember(content, **kwargs) -> str` | Store to working memory |
| `recall(query, **kwargs) -> list` | Hybrid search across all tiers |
| `sleep() -> dict` | Consolidate working → episodic |
| `invalidate(memory_id, replacement_id=None) -> bool` | Mark memory as superseded |
| `scratchpad_write(key, value) -> str` | Write to scratchpad |
| `scratchpad_read(key) -> str` | Read from scratchpad |
| `scratchpad_clear() -> int` | Clear scratchpad |

---

## Memory Banks

**Module:** `mnemosyne.core.banks`

```python
from mnemosyne.core.banks import BankManager

manager = BankManager(data_dir="~/.hermes/mnemosyne/data")

# Create a bank
manager.create_bank("work")

# List banks
banks = manager.list_banks()

# Check if bank exists
exists = manager.bank_exists("work")

# Get bank stats
stats = manager.get_bank_stats("work")

# Rename a bank
manager.rename_bank("work", "work-v2")

# Delete a bank
manager.delete_bank("work")
```

---

## Entity Extraction

**Module:** `mnemosyne.core.entities`

```python
from mnemosyne.core.entities import (
    extract_entities_regex,
    levenshtein_distance,
    find_similar_entities
)

# Extract entities from text
entities = extract_entities_regex("Abdias founded Mnemosyne in New York")
# Returns: ["Abdias", "Mnemosyne", "New York"]

# Fuzzy match
distance = levenshtein_distance("Abdias", "Abdias J")

# Find similar entities in a list
matches = find_similar_entities("Abdias", ["Abdias J", "Python", "New York"], threshold=0.7)
```

---

## Fact Extraction

**Module:** `mnemosyne.core.extraction`

```python
from mnemosyne.core.extraction import extract_facts, extract_facts_safe

# Extract facts (may raise if no LLM available)
facts = extract_facts("Mnemosyne uses SQLite for storage and fastembed for embeddings.")
# Returns: list of fact strings

# Safe wrapper (never raises, returns empty list on failure)
facts = extract_facts_safe("Some text to extract facts from")
```

**Fallback chain:** Remote OpenAI API → Local ctransformers GGUF → Skip (returns [])

---

## Streaming & Delta Sync

**Module:** `mnemosyne.core.streaming`

### MemoryStream

```python
from mnemosyne.core.streaming import MemoryStream

stream = MemoryStream()

# Push events
stream.push("remember", {"id": "abc", "content": "test"})

# Pull via callback
stream.on_event(lambda event: print(event))

# Pull via iterator
for event in stream:
    process(event)
```

### DeltaSync

```python
from mnemosyne.core.streaming import DeltaSync

sync = DeltaSync(mnemosyne_instance)

# Compute changes since last checkpoint
delta = sync.compute_delta()

# Apply delta to another instance
sync.apply_delta(delta)

# Full bidirectional sync
sync.sync_to(other_mnemosyne)
sync.sync_from(other_mnemosyne)
```

---

## Pattern Detection & Compression

**Module:** `mnemosyne.core.patterns`

### PatternDetector

```python
from mnemosyne.core.patterns import PatternDetector

detector = PatternDetector()

# Detect temporal patterns (hour-of-day, day-of-week)
temporal = detector.detect_temporal_patterns(memories)

# Detect content patterns (keyword frequency, co-occurrence)
content = detector.detect_content_patterns(memories)

# Detect sequence patterns
sequences = detector.detect_sequence_patterns(memories)
```

### MemoryCompressor

```python
from mnemosyne.core.patterns import MemoryCompressor

compressor = MemoryCompressor()

# Compress memories
compressed = compressor.compress(memories)

# Decompress
decompressed = compressor.decompress(compressed)

# Batch compress
batch = compressor.compress_batch(memory_list)
```

---

## Plugin System

**Module:** `mnemosyne.core.plugins`

### Creating a Plugin

```python
from mnemosyne.core.plugins import MnemosynePlugin

class MyPlugin(MnemosynePlugin):
    name = "my-plugin"
    
    def on_remember(self, memory_id, content, **kwargs):
        """Called after a memory is stored."""
        pass
    
    def on_recall(self, query, results, **kwargs):
        """Called after recall. Can modify results."""
        return results
    
    def on_consolidate(self, count, **kwargs):
        """Called after sleep() consolidation."""
        pass
    
    def on_invalidate(self, memory_id, **kwargs):
        """Called after a memory is invalidated."""
        pass
```

### PluginManager

```python
from mnemosyne.core.plugins import PluginManager

pm = PluginManager()

# Register a plugin
pm.register(MyPlugin())

# Load from directory (auto-discovers .py files)
pm.discover("~/.hermes/mnemosyne/plugins/")

# Unregister
pm.unregister("my-plugin")

# List loaded plugins
plugins = pm.list_plugins()
```

---

## TripleStore (Knowledge Graph)

**Module:** `mnemosyne.core.triples`

```python
from mnemosyne.core.triples import TripleStore

store = TripleStore(db_path)

# Add a triple
store.add_triple("user_123", "prefers", "dark_mode")

# Query triples
results = store.query_triples(subject="user_123")

# Get all triples for an entity
triples = store.get_triples_for_subject("memory_id_abc")
```

---

## MCP Server

**Module:** `mnemosyne.mcp_server`

```bash
# stdio transport (for Claude Desktop, etc.)
mnemosyne mcp

# SSE transport (for web clients)
mnemosyne mcp --transport sse --port 8080

# Scoped to a specific bank
mnemosyne mcp --bank project_a
```

### MCP Tools

| Tool | Description |
|---|---|
| `mnemosyne_remember` | Store a memory |
| `mnemosyne_recall` | Search memories |
| `mnemosyne_sleep` | Run consolidation |
| `mnemosyne_scratchpad_read` | Read scratchpad |
| `mnemosyne_scratchpad_write` | Write to scratchpad |
| `mnemosyne_get_stats` | Get memory statistics |

---

## CLI

```bash
mnemosyne store "User prefers dark mode" --importance 0.9
mnemosyne recall "user preferences" --top-k 10
mnemosyne update <memory_id> --content "Updated content"
mnemosyne delete <memory_id>
mnemosyne stats
mnemosyne sleep
mnemosyne export backup.json
mnemosyne import backup.json
mnemosyne bank list
mnemosyne bank create work
mnemosyne bank delete work
mnemosyne mcp
mnemosyne diagnose
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MNEMOSYNE_DATA_DIR` | `~/.hermes/mnemosyne/data/` | Root data directory |
| `MNEMOSYNE_VEC_TYPE` | `int8` | Vector storage type: `bit` (48 bytes), `int8` (384 bytes), `float32` (1536 bytes) |
| `MNEMOSYNE_SESSION_ID` | Auto UUID | Default session identifier |
| `MNEMOSYNE_TEMPORAL_HALFLIFE_HOURS` | `24` | Default temporal decay halflife |
| `FASTEMBED_CACHE_PATH` | `~/.hermes/cache/fastembed` | FastEmbed model cache directory |

---

*API reference generated from source code. Every method and parameter verified against actual implementation.*
