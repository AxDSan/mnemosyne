# mnemosyne — PR #129: CompressionPlugin Not Loading (Root Cause Found)

## Status: ROOT CAUSE IDENTIFIED — Fix Pending

## Bug Summary

`CompressionPlugin` is registered in `PluginManager._registry` but **never loaded** into `_instances`. The call chain in `beam.py` hits `get_plugin()` which looks in `_instances` (not `_registry`), so it always returns `None`.

## Root Cause

### 1. Registration happens, loading never does

```
PluginManager.__init__()
  → register_plugin("compression", CompressionPlugin)   ✓ adds to _registry
  → _instances remains empty

beam.py line 4521:
  _plugins.get_manager().get_plugin("compression")
  → searches _instances → NOT _registry → returns None ✗

CompressionPlugin.enabled = False (class default)
  → self._compress() never called
```

### 2. Two separate CompressionPlugin files exist

| File | Purpose | Status |
|------|---------|--------|
| `mnemosyne/core/plugins.py` | Built-in, registered at module load | Not loaded |
| `mnemosyne/plugins/compression.py` | External plugin, discovered via `discover_plugins()` | Never discovered |

`discover_plugins()` is never called in the production code path:
- Not called in `PluginManager.__init__`
- Not called in any startup/shutdown path
- Only called in tests

### 3. The external plugin has a different signature

`mnemosyne/plugins/compression.py` uses `config: Dict[str, Any]` but `mnemosyne/core/plugins.py`'s `CompressionPlugin` uses `config: dict = None`. Minor inconsistency.

### 4. `enabled` is False by default

```python
# mnemosyne/core/plugins.py line 345
enabled = False  # Opt-in; must be explicitly enabled via config or deprecated env var
```

Even if `load_plugin("compression")` were called with config `{enabled: True}`, the plugin would work. But loading never happens.

---

## Why CI Passes But Production Fails

CI tests use `manager.load_plugin("compression", {"enabled": True})` directly:
```python
def test_enabled_via_config(self, manager):
    instance = manager.load_plugin("compression", {"enabled": True})
    assert instance.enabled  # ✓ Works in test
```

But in production, nobody calls `load_plugin("compression", ...)`. `get_plugin()` returns None.

---

## Required Fix

One of these solutions is needed:

### Option A (minimal): Call `load_plugin("compression")` at startup
Add to mnemosyne initialization (e.g., `beam.py` or a `setup()` call):
```python
_plugins.get_manager().load_plugin("compression")
```
But this still requires config to enable it.

### Option B (proper): Wire CompressionPlugin into MnemosyneConfig
`MnemosyneConfig` should read `compression.enabled` from config and call `manager.load_plugin("compression", config)`.

### Option C (minimal+): Fix `get_plugin` to auto-load registered plugins
Change `get_plugin()` to check `_registry` for registered-but-not-loaded plugins and auto-load them with no config.

---

## Files to Modify

1. `mnemosyne/core/plugins.py` — fix the loading path (Option B or C)
2. Potentially `mnemosyne/core/beam.py` — wire config into plugin initialization

## Verification

```python
# This works in test but not in production:
from mnemosyne.core import plugins as _plugins
_plugins.get_manager().get_plugin("compression")  # → None in production, plugin in tests
```

## Branch State
- `feature/compression-plugin` — has all the CompressionPlugin code
- Latest commit: `07cadb6` (merge from main)
- Working tree: clean
- Need to push findings and implement fix