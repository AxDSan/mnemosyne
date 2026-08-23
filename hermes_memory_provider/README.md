# Mnemosyne Memory Provider for Hermes

Deploy Mnemosyne as a **first-class MemoryProvider** through Hermes' plugin system.

## What This Gives You

When deployed, Mnemosyne gets the **same integration tier** as Honcho, mem0, and supermemory:

- **System prompt injection** — `# Mnemosyne Memory` header in every prompt
- **Pre-turn prefetch** — Relevant memories injected via `<memory-context>` fence before each API call
- **Post-turn sync** — User and assistant messages automatically stored to episodic memory
- **Tool dispatch** — named tool surface: full 40 / slim 21 / none 14 (library default `full`)
- **CLI commands** — `hermes mnemosyne {stats|sleep|version|inspect|clear|export|import}`
- **Setup wizard** — Listed in `hermes memory setup`

**All of this without touching Hermes core.** Deployed purely through the plugin directory.

## Deploy

```bash
# One-time setup: symlink into Hermes plugin directory
ln -s $(pwd)/hermes_memory_provider ~/.hermes/plugins/mnemosyne

# Activate in config
hermes config set memory.provider mnemosyne
```

Or manually edit `~/.hermes/config.yaml`:

```yaml
memory:
  provider: mnemosyne
  mnemosyne:
    auto_sleep: true
    sleep_threshold: 50
    vector_type: float32  # float32 | int8 | bit
    interactive_writes: full  # full | slim | none
    prefetch_char_limit: 3000  # total assembled per-turn injection budget
```

`interactive_writes` is an init-time named preset over the advertised tool surface (`full` default, upstream-safe). `slim` keeps everyday reads plus remember/update/forget; `none` is read-only. An explicit `tools:` list — including `[]` — still wins over the mode. Changing the knob mid-conversation invalidates Hermes' cached tool-schema prefix, so restart after editing it; do not flip the mode in `on_turn_start`.

`prefetch_char_limit` caps the assembled per-turn injection (identity first, then later hits) at 3000 characters by default; omitted hits append a footer pointing at `mnemosyne_recall`.

## Operator notes (harness extract)

Interactive turns and sleep-time writes are split so the live model does not pay the full write-tool tax.

### Tool-surface modes

| Mode | Tools | Role |
|------|------:|------|
| `full` | 40 | Library default. Upstream-safe; every advertised schema. |
| `slim` | 21 | Everyday reads plus remember / update / forget (and the rest of the slim write set). |
| `none` | 14 | Read-only. |

This machine may opt into `slim` with `hermes config set memory.mnemosyne.interactive_writes slim`. Restart Hermes after changing it. Do not flip the mode mid-session — that invalidates the cached tool-schema prefix.

An explicit `tools:` list — including `[]` — wins over the mode.

### Prefetch budget

`prefetch_char_limit` (default **3000**) is the assembled per-turn injection budget. Identity-ranked hits fill first; omitted hits append a footer pointing at `mnemosyne_recall`.

`MNEMOSYNE_PREFETCH_CONTENT_CHARS` is a different knob: a per-memory content cap (`0` = full content). It does not replace the 3000 assembled budget.

### Sleep

Sleep honors `auxiliary.sleep` when that slot has a provider or model, then falls back to `auxiliary.compression`. A missing sleep slot must **not** use bare `task=sleep` (that would fall through to the main model).

Do not copy a live `auxiliary.sleep` YAML block onto this machine as if it were required. Inspect resolution with:

```bash
hermes mnemosyne sleep --dry-run
```

That prints the resolved aux slot and trajectory record counts. No LLM call, no writes.

When Hermes session messages exist, sleep is trajectory-first. Otherwise it falls back to working-memory rows. The dry-run / trajectory path never dumps tool XML.

Canonical sleep slots are `preference`, `identity`, and `environment`. Slot bodies are ≤400 characters. Sleep never writes `SOUL.md`.

### Ship gate

```bash
uv run python hermes_memory_provider/scripts/extract_gate.py
```

Last measured: slim **3321** vs full **6645** (50.02% drop), prefetch p95 **2826**, `ship_ok` true.

## Verify

```bash
hermes memory status    # Should show "mnemosyne" as active provider
hermes mnemosyne stats  # Show memory statistics
```

## Architecture

```
~/.hermes/plugins/mnemosyne/   ← symlink to hermes_memory_provider/
├── __init__.py                  ← MnemosyneMemoryProvider (MemoryProvider ABC)
├── cli.py                       ← hermes mnemosyne subcommands
├── plugin.yaml                  ← Manifest for discovery
└── README.md                    ← This file
```

The provider is discovered by `plugins.memory.discover_memory_providers()` which scans:
1. Bundled providers: `hermes-agent/plugins/memory/<name>/`
2. **User plugins: `$HERMES_HOME/plugins/<name>/`** ← This is where Mnemosyne lives

User plugins take precedence over bundled plugins on name collision.

## Tools (Auto-Injected)

Full advertised surface is **40** schemas. `slim` keeps **21**; `none` keeps **14**. The table below is the original everyday set, not the full inventory.

| Tool | Purpose |
|------|---------|
| `mnemosyne_remember` | Store durable memory with importance, scope, expiry |
| `mnemosyne_recall` | Hybrid search (50% vector + 30% FTS + 20% importance) |
| `mnemosyne_stats` | Show working + episodic counts |
| `mnemosyne_triple_add` | Add temporal facts to the knowledge graph |
| `mnemosyne_triple_query` | Query temporal knowledge graph facts |
| `mnemosyne_sleep` | Consolidate working → episodic memory |
| `mnemosyne_scratchpad_write` | Write short-lived scratchpad context |
| `mnemosyne_scratchpad_read` | Read scratchpad context |
| `mnemosyne_scratchpad_clear` | Clear scratchpad context |
| `mnemosyne_invalidate` | Mark memory as expired/superseded |
| `mnemosyne_export` | Export memories for backup or migration |
| `mnemosyne_import` | Import memories from backup files or providers |
| `mnemosyne_update` | Update an existing memory |
| `mnemosyne_forget` | Delete a memory |
| `mnemosyne_diagnose` | Run diagnostics on the memory store |

## Undeploy

```bash
rm ~/.hermes/plugins/mnemosyne
hermes config set memory.provider null
```

Hermes falls back to built-in memory only.
