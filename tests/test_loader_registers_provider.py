"""[#565] Integration tests: root-repo layout registers the memory provider.

The Hermes memory provider loader (plugins/memory/__init__.py) discovers a
provider directory by text-scanning its top-level ``__init__.py`` for
``register_memory_provider`` or ``MemoryProvider``, then loads the module and
drives registration through one of two entry points:

1. ``mod.register(collector)`` — the collector's
   ``register_memory_provider(provider)`` captures the single active provider.
2. a top-level ``MemoryProvider`` subclass, instantiated directly.

These tests prove the root-repo (and root-repo symlink) install layout
satisfies that contract: ``register`` registers exactly one
``MnemosyneMemoryProvider`` with a loader-shaped collector, and — where the
real loader is importable — ``load_memory_provider("mnemosyne")`` returns the
provider instance.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_register_registers_single_provider_in_root_repo_layout():
    """mod.register(collector) captures exactly one MnemosyneMemoryProvider.

    Runs in a subprocess with the plugin dir symlinked to the repo root (the
    "root-repo symlink layout"), loading the module the same way
    plugins.memory._load_provider_from_dir does: spec_from_file_location with
    submodule_search_locations pointing at the plugin directory.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        plugins_dir = tmp / "plugins"
        plugins_dir.mkdir()
        plugin_dir = plugins_dir / "mnemosyne"
        plugin_dir.symlink_to(REPO_ROOT, target_is_directory=True)

        script = textwrap.dedent("""\
            import importlib.util
            import sys
            from pathlib import Path

            plugin_dir = Path({plugin_dir!r})

            # Mirror plugins.memory._register_synthetic_package() so relative
            # imports inside the plugin (from .mnemosyne ...) resolve.
            import importlib.machinery

            ns = "_itest_ns"
            spec = importlib.machinery.ModuleSpec(ns, None, is_package=True)
            spec.submodule_search_locations = []
            sys.modules[ns] = importlib.util.module_from_spec(spec)

            spec = importlib.util.spec_from_file_location(
                ns + ".mnemosyne",
                str(plugin_dir / "__init__.py"),
                submodule_search_locations=[str(plugin_dir)],
            )
            mod = importlib.util.module_from_spec(spec)
            sys.modules[ns + ".mnemosyne"] = mod
            spec.loader.exec_module(mod)

            assert callable(mod.register), "loader entry point register() missing"


            class Collector:
                \"\"\"Shape of plugins.memory._ProviderCollector.\"\"\"

                def __init__(self):
                    self.provider = None
                    self.calls = []

                def register_memory_provider(self, provider):
                    self.calls.append("memory_provider")
                    self.provider = provider

                def register_tool(self, *a, **k):
                    self.calls.append("tool")

                def register_hook(self, *a, **k):
                    self.calls.append("hook")

                def register_cli_command(self, *a, **k):
                    self.calls.append("cli_command")


            collector = Collector()
            mod.register(collector)

            assert collector.provider is not None, "no provider captured by collector"
            assert collector.calls.count("memory_provider") == 1, collector.calls

            # Class-name check is namespace-agnostic: the loader imports the
            # plugin under a synthetic namespace (_itest_ns / the real
            # _hermes_user_memory), so a top-level `import` of the class is a
            # *different* module object than the one the bridge imported.
            assert type(collector.provider).__name__ == "MnemosyneMemoryProvider", (
                type(collector.provider)
            )

            print("PASS")
        """).format(plugin_dir=str(plugin_dir))

        result = subprocess.run(
            [sys.executable, "-W", "ignore::DeprecationWarning", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(tmp),
        )

    assert result.returncode == 0, (
        f"subprocess failed (exit {result.returncode}):\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "PASS" in result.stdout, result.stdout


def test_actual_loader_loads_mnemosyne_provider(tmp_path, monkeypatch):
    """End-to-end: the real plugins.memory loader returns the provider.

    Requires the Hermes agent package (plugins.memory) to be importable;
    skipped in this repo's standalone CI, exercised in Hermes deployments.
    """
    pytest.importorskip("plugins.memory")
    from plugins.memory import load_memory_provider

    # Dev-box artifact: when the repo checkout lives directly under
    # $HERMES_HOME/plugins/, pytest's namespace-package detection puts that
    # parent dir on sys.path, so conftest's autouse fixture (which imports
    # mnemosyne to reset caches) can cache the plugin *stub* as top-level
    # `mnemosyne` — a module with no `.core`. A real Hermes process never
    # imports `mnemosyne` before the loader runs, so the loader must see the
    # real core package. Drop any shadowed cache entry first.
    import sys as _sys

    _cached = _sys.modules.get("mnemosyne")
    if _cached is not None and not hasattr(_cached, "core"):
        _sys.modules.pop("mnemosyne", None)

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    plugin_dir = plugins_dir / "mnemosyne"
    plugin_dir.symlink_to(REPO_ROOT, target_is_directory=True)

    # Point the loader at the temp layout via HERMES_HOME (get_hermes_home()
    # reads this env var).
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    provider = load_memory_provider("mnemosyne")

    assert provider is not None, "load_memory_provider('mnemosyne') returned None"
    # Namespace-agnostic check (same rationale as test 1): the loader imports
    # the plugin under the synthetic _hermes_user_memory namespace, so a
    # top-level `import` of the class is a different module object.
    assert type(provider).__name__ == "MnemosyneMemoryProvider", type(provider)
    assert provider.name == "mnemosyne", provider.name
