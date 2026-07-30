# Mnemosyne + Hermes Agent

Mnemosyne is the native memory provider for Hermes Agent. Two integration paths:

## Path 1: MCP (recommended for latest)

Add to your Hermes `config.yaml`:

```yaml
mcp:
  servers:
    mnemosyne:
      command: mnemosyne
      args: ["mcp"]
```

Tools register as native Hermes commands.

## Path 2: Hermes Plugin (built-in)

```bash
pip install mnemosyne-hermes
mnemosyne-hermes install                  # creates plugin symlink for Hermes discovery
hermes config set memory.provider mnemosyne
hermes memory setup
```

This gives you the **20 tools** declared in `plugin.yaml` (remember, recall, forget, stats, knowledge graph ops, scratchpad, export/import, sync), plus the persona and canonical tools the provider implements beyond that declaration. All native Hermes commands. See the [generated tool schema reference](../api/tool-schema.mdx) for the full surface.

## Usage

In Hermes, use the built-in commands:
- `mnemosyne_remember` — Store a memory
- `mnemosyne_recall` — Search memories
- `mnemosyne_forget` — Remove a memory
- `mnemosyne_stats` — View memory statistics
- `mnemosyne_sleep` — Run consolidation
