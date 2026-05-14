# mnemosyne — Continue Here

**Status:** PR #139 filed (provider timeouts), PR #129 open (compression plugin)
**HEAD:** `d701a2e` on `fix/provider-timeout-env-vars`
**Last Updated:** 2026-05-14 18:45 UTC

---

## Open PRs (AxDSan/mnemosyne)

| PR | Title | Status |
|----|-------|--------|
| **#139** | fix(provider): make three hardcoded timeouts configurable via env vars | **Open** — filed from ether-btc/fix/provider-timeout-env-vars |
| **#129** | feat(plugins): CompressionPlugin — replace hardcoded env-var compression | **Open** — filed from ether-btc/feature/compression-plugin (8ee9c92) |

---

## PR #139 — Provider Timeout Env Vars

**Filed against:** `AxDSan/mnemosyne:main`

| Constant | Before | Env Var | Default |
|----------|--------|---------|---------|
| `SESSION_END_SLEEP_TIMEOUT_SECONDS` | `15` | `MNEMOSYNE_SESSION_END_TIMEOUT` | `60` |
| Auto-sleep `join(timeout=5)` | hardcoded `5` | `MNEMOSYNE_AUTO_SLEEP_TIMEOUT` | `15` |
| `SHUTDOWN_DRAIN_TIMEOUT_SECONDS` | `2` | `MNEMOSYNE_SHUTDOWN_DRAIN_TIMEOUT` | `8` |

**Files changed:**
- `hermes_memory_provider/__init__.py`: 3 constants now read from env
- `tests/test_hermes_memory_provider.py`: default tests updated to 60s and 8s

**CI:** `test_hermes_memory_provider.py` — 43 passed

---

## PR #129 — CompressionPlugin

**Filed against:** `AxDSan/mnemosyne:main`

3 fixes on `feature/compression-plugin` (commit `8ee9c92`):
1. ✅ `get_plugin()` lazy-loads registered plugins (91244eb — prior session)
2. ✅ Deleted dead external `mnemosyne/plugins/compression.py`
3. ✅ Added `test_sleep_loads_compression_plugin_and_enables_via_config` integration test

---

## Merged This Session

| PR | Note |
|----|------|
| **#138** | EMBEDDING_DIM env var — merged at 2026-05-14T16:40:10Z |
| **#136** | Sleep prompt override — closed by author at 2026-05-14T16:39:10Z |
| **#138** supersedes #131 | Original EMBEDDING_DIM PR |

---

## Pre-existing Failures (unrelated to our changes)

`mnemosyne/core/recall_diagnostics.py:40` — `AttributeError: module 'logging' has no attribute 'getLogger'`
- Causes ~20 test failures in `TestEpisodicMemory`, `TestCrossSessionRecall`, etc.
- Separate bug in that module — not touched by any of our changes
- Tests that don't import `recall_diagnostics` pass cleanly

---

## Git State

```
Branch:      fix/provider-timeout-env-vars (pushed to origin)
PR:          #139 → AxDSan/mnemosyne:main
Working tree: clean
```