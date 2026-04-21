# DEVOPS PLAN: Mnemosyne First-Class Memory Provider Integration
## Sprint: Memory Provider Tier Ascension
**Owner:** Hermes Agent (AI-assisted)  
**Start:** 2026-04-19  
**Target:** Single commit, clean push to origin/main

---

## 1. Objective
Promote Mnemosyne from a general plugin (tool+hook registration) to a **first-class MemoryProvider** in Hermes, achieving architectural parity with Honcho, mem0, supermemory, and other bundled memory backends.

**Success Criteria:**
- [ ] Mnemosyne appears in `discover_memory_providers()` alongside Honcho
- [ ] `memory.provider: mnemosyne` in config.yaml activates Mnemosyne via MemoryManager
- [ ] Automatic tool injection (no LLM "choice" required)
- [ ] `<memory-context>` fence injection before every LLM call
- [ ] Post-turn sync (`sync_all`) stores conversation to episodic memory
- [ ] CLI command `hermes mnemosyne` for stats/sleep/inspect
- [ ] Setup wizard lists Mnemosyne as an option
- [ ] Zero regressions in existing Mnemosyne functionality

---

## 2. Architecture Overview

```
Hermes Agent
├── plugins/memory/                    ← Bundled memory providers
│   ├── honcho/                        ← External cloud API
│   ├── mem0/                          ← External cloud API
│   ├── supermemory/                   ← External cloud API
│   ├── mnemosyne/   ← NEW            ← Local, zero-cloud, first-class
│   │   ├── __init__.py                ← MnemosyneMemoryProvider class
│   │   ├── cli.py                     ← hermes mnemosyne subcommand
│   │   ├── plugin.yaml                ← Manifest
│   │   └── README.md
│   └── __init__.py                    ← Plugin discovery loader
│
└── Mnemosyne Core (separate repo)
    ├── mnemosyne/core/beam.py
    ├── mnemosyne/core/memory.py
    └── hermes_plugin/                 ← Legacy general plugin (deprecated)
```

**Key Insight:** The MemoryProvider wrapper lives IN Hermes (`plugins/memory/mnemosyne/`), but IMPORTS Mnemosyne core from its installed location. This is the same pattern Honcho uses.

---

## 3. Implementation Phases

### Phase 1: MemoryProvider Wrapper (CORE)
**File:** `plugins/memory/mnemosyne/__init__.py`

Implement `MnemosyneMemoryProvider` extending `MemoryProvider` ABC:

| Method | Purpose |
|--------|---------|
| `name` | Return `"mnemosyne"` |
| `is_available()` | Check if `mnemosyne` package is importable |
| `initialize()` | Init Mnemosyne with session_id, create tables |
| `system_prompt_block()` | Return `# Mnemosyne Memory` header with mode info |
| `prefetch(query)` | Call `beam.get_context(limit=10)` for pre-turn injection |
| `sync(user_msg, assistant_msg)` | Call `beam.remember()` for both turns |
| `get_tool_schemas()` | Return schemas for all Mnemosyne tools |
| `handle_tool_call(name, args)` | Dispatch to beam functions |
| `save_config()` / `get_config_schema()` | Config persistence |

**Tool Mapping:**
| MemoryProvider Tool | Mnemosyne Function |
|---------------------|-------------------|
| `mnemosyne_remember` | `beam.remember()` |
| `mnemosyne_recall` | `beam.recall()` |
| `mnemosyne_sleep` | `beam.sleep()` |
| `mnemosyne_stats` | `beam.stats()` |
| `mnemosyne_invalidate` | `beam.invalidate()` |
| `mnemosyne_triple_add` | `mnemosyne_triple_add()` |
| `mnemosyne_triple_query` | `mnemosyne_triple_query()` |

### Phase 2: CLI Integration
**File:** `plugins/memory/mnemosyne/cli.py`

Subcommands:
- `hermes mnemosyne stats` — Show memory stats (working/episodic counts, BEAM tiers)
- `hermes mnemosyne sleep` — Run consolidation cycle
- `hermes mnemosyne inspect <query>` — Search memories
- `hermes mnemosyne clear` — Clear scratchpad (with confirmation)

### Phase 3: Setup Wizard Integration
**File:** `hermes_cli/memory_setup.py` (patch existing)

Add Mnemosyne as a provider option in:
1. `hermes memory setup` interactive wizard
2. Auto-detection of installed Mnemosyne package

### Phase 4: Config Schema
**File:** `hermes_cli/config.py` (patch existing)

Add Mnemosyne config defaults:
```yaml
memory:
  provider: mnemosyne  # or honcho, mem0, etc.
  mnemosyne:
    session_id: hermes_default
    vector_type: float32  # float32 | int8 | bit
    auto_sleep: true      # Run sleep() every N turns
    sleep_threshold: 50   # Working memory count before auto-sleep
```

### Phase 5: Legacy Plugin Deprecation
**File:** `mnemosyne/hermes_plugin/__init__.py` (patch)

Add deprecation warning when loaded via general plugin system:
```python
logger.warning("Mnemosyne general plugin is deprecated. Use memory.provider: mnemosyne in Hermes config instead.")
```

### Phase 6: Testing
**Files:** New test files in Hermes test suite

- `test_mnemosyne_provider.py` — Unit tests for MnemosyneMemoryProvider
- `test_mnemosyne_cli.py` — CLI command tests
- Integration test: Set `memory.provider: mnemosyne`, verify tools appear, verify context injection

### Phase 7: Documentation
**Files:** README updates

- Update Mnemosyne README with MemoryProvider setup instructions
- Update Hermes memory setup docs

---

## 4. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Import path issues (Mnemosyne not in sys.path) | Medium | High | Robust fallback logic in wrapper |
| Schema conflicts with legacy plugin | Low | Medium | Legacy plugin auto-disables when provider active |
| Context injection double-fire (hook + provider) | Medium | Medium | Legacy hook detects provider mode and no-ops |
| Config migration confusion | Low | Low | Clear deprecation message, backward compat |

---

## 5. Execution Log

| Step | Status | Commit |
|------|--------|--------|
| Phase 1: MemoryProvider wrapper | PENDING | |
| Phase 2: CLI integration | PENDING | |
| Phase 3: Setup wizard | PENDING | |
| Phase 4: Config schema | PENDING | |
| Phase 5: Legacy deprecation | PENDING | |
| Phase 6: Testing | PENDING | |
| Phase 7: Documentation | PENDING | |
| Final commit + push | PENDING | |

---

## 6. Notes

- **Hermes repo path:** `/root/.hermes/hermes-agent/`
- **Mnemosyne repo path:** `/root/.hermes/projects/mnemosyne/`
- **MemoryProvider ABC:** `hermes-agent/agent/memory_provider.py`
- **MemoryManager:** `hermes-agent/agent/memory_manager.py`
- **Plugin discovery:** `hermes-agent/plugins/memory/__init__.py`
- **Pattern to follow:** `hermes-agent/plugins/memory/honcho/__init__.py`
