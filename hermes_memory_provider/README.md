# Mnemosyne Memory Provider for Hermes

Deploy Mnemosyne as a **first-class MemoryProvider** through Hermes' plugin system.

## What This Gives You

When deployed, Mnemosyne gets the **same integration tier** as Honcho, mem0, and supermemory:

- **System prompt injection** — `# Mnemosyne Memory` header in every prompt
- **Pre-turn prefetch** — Relevant memories injected via `<memory-context>` fence before each API call
- **Post-turn sync** — User and assistant messages automatically stored to episodic memory
- **Tool dispatch** — 7 memory tools auto-injected into the model's tool surface
- **CLI commands** — `hermes mnemosyne {stats|sleep|inspect|clear}`
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
```

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

| Tool | Purpose |
|------|---------|
| `mnemosyne_remember` | Store durable memory with importance, scope, expiry |
| `mnemosyne_recall` | Hybrid search (50% vector + 30% FTS + 20% importance) |
| `mnemosyne_sleep` | Consolidate working → episodic memory; pass `all_sessions=true` for inactive-session maintenance |
| `mnemosyne_stats` | Show working + episodic counts |
| `mnemosyne_invalidate` | Mark memory as expired/superseded |
| `mnemosyne_triple_add` | Add temporal fact to knowledge graph |
| `mnemosyne_triple_query` | Query knowledge graph |

## Undeploy

```bash
rm ~/.hermes/plugins/mnemosyne
hermes config set memory.provider null
```

Hermes falls back to built-in memory only.
