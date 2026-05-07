"""
Shared test fixtures for Mnemosyne test suite.

Provides fixtures that handle SQLite thread-local connection cleanup
to prevent "database is locked" and UNIQUE constraint collisions
between tests.
"""

import pytest


def _close_cached_connections():
    """Close and reset thread-local SQLite connection caches in both modules."""
    for mod_path in (
        "mnemosyne.core.beam",
        "mnemosyne.core.memory",
    ):
        try:
            import importlib
            mod = importlib.import_module(mod_path)
            tl = getattr(mod, "_thread_local", None)
            if tl is not None and hasattr(tl, "conn") and tl.conn is not None:
                try:
                    tl.conn.close()
                except Exception:
                    pass
                tl.conn = None
                if hasattr(tl, "db_path"):
                    tl.db_path = None
        except Exception:
            pass

    # Reset the global Mnemosyne default instance to avoid cross-test
    # contamination of the singleton
    try:
        from mnemosyne.core import memory as _mem_mod
        _mem_mod._default_instance = None
        _mem_mod._default_bank = "default"
    except Exception:
        pass

    # Reset hermes_plugin singleton
    try:
        import hermes_plugin
        hermes_plugin._memory_instance = None
        hermes_plugin._current_session_id = None
        hermes_plugin._triple_store = None
    except Exception:
        pass

    # Reset host LLM backend registry to prevent cross-test contamination.
    # The registry is a process-global; a test that forgets to unregister
    # would otherwise bleed into the next.
    try:
        from mnemosyne.core import llm_backends as _llm_backends_mod
        _llm_backends_mod._backend = None
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_thread_local_connections():
    """
    Auto-use fixture that resets thread-local SQLite connection caches
    before and after every test. This prevents connection leakage between
    tests that use different database paths.

    Both mnemosyne.core.beam and mnemosyne.core.memory maintain their own
    thread-local caches (_thread_local.conn / _thread_local.db_path).
    When tests create instances with different db_paths, the old connection
    is never closed, leading to "database is locked" errors.
    """
    _close_cached_connections()
    yield
    _close_cached_connections()
