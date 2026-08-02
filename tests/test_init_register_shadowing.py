"""[#565] Regression test: register dispatch must preserve both paths.

When the repo top-level __init__.py is loaded in a Hermes plugin environment
(hermes_plugin is importable), `register` must still be the loader entry
point that dispatches on ctx shape:

- a memory-provider collector (has ``register_memory_provider``) routes to
  the provider bridge — the case the real loader always hits — and the
  legacy path is NOT invoked;
- any other ctx preserves the legacy hermes_plugin.register registration
  path (no shadowing).

Post-fix, the memory-provider bridge uses the name `register_memory_provider`
(which is what plugins/memory/__init__.py probes for), so both paths coexist
without shadowing.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_mock_hermes_plugin(tmpdir: Path) -> Path:
    """Create a mock hermes_plugin package (tracks calls)."""
    pkg = tmpdir / "hermes_plugin"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        textwrap.dedent("""\
            calls = []
            def register(ctx):
                calls.append(ctx)
                return "hermes_plugin_register_called"
        """)
    )
    return pkg.parent


def test_hermes_plugin_register_not_shadowed():
    """When hermes_plugin is importable, mnemosyne.register must be the original."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        mock_root = _make_mock_hermes_plugin(tmp / "mock_packages")

        plugin_parent = tmp / "plugin_parent"
        plugin_parent.mkdir()
        plugin_package = plugin_parent / "mnemosyne"
        plugin_package.symlink_to(REPO_ROOT, target_is_directory=True)

        script = textwrap.dedent("""\
            import importlib.machinery
            import importlib.util
            import sys
            from pathlib import Path

            sys.path.insert(0, {mock_root!r})
            blocked = {{{repo_root!r}, {repo_parent!r}}}
            sys.path = [p for p in sys.path if p not in blocked]

            import hermes_plugin

            # Load the plugin module under a synthetic namespace, mirroring
            # plugins.memory._load_provider_from_dir (which uses
            # _hermes_user_memory.<name>): a plain top-level `import mnemosyne`
            # would bind the stub name, so the bridge's own
            # `from mnemosyne.core...` inside hermes_memory_provider would
            # resolve to the stub instead of the real core package (dev-box
            # Pitfall 10). The namespaced load keeps the stub name free, so
            # those imports resolve through sys.path exactly as in production.
            ns = "shadowing_ns"
            spec = importlib.machinery.ModuleSpec(ns, None, is_package=True)
            spec.submodule_search_locations = []
            sys.modules[ns] = importlib.util.module_from_spec(spec)

            plugin_dir = Path({plugin_dir!r})
            spec = importlib.util.spec_from_file_location(
                ns + ".mnemosyne",
                str(plugin_dir / "__init__.py"),
                submodule_search_locations=[str(plugin_dir)],
            )
            mnemosyne = importlib.util.module_from_spec(spec)
            sys.modules[ns + ".mnemosyne"] = mnemosyne
            spec.loader.exec_module(mnemosyne)

            # 1. register must exist and preserve the legacy path for a
            #    non-collector ctx (hermes_plugin importable)
            assert hasattr(mnemosyne, 'register'), (
                "expected mnemosyne.register when hermes_plugin is importable"
            )
            result = mnemosyne.register("dummy_ctx")
            assert result == "hermes_plugin_register_called", (
                "mnemosyne.register returned " + repr(result) + ", "
                "expected 'hermes_plugin_register_called' - "
                "register was shadowed"
            )
            assert hermes_plugin.calls == ["dummy_ctx"], hermes_plugin.calls

            # 2. A memory-provider collector (the shape plugins.memory's
            #    _ProviderCollector exposes) must route to the provider bridge —
            #    the exact case the real loader hits when hermes_plugin is
            #    importable (dplush review round 2 on #565). The legacy path
            #    must NOT be invoked for a collector.
            class Collector:
                def __init__(self):
                    self.provider = None

                def register_memory_provider(self, provider):
                    self.provider = provider

            collector = Collector()
            mnemosyne.register(collector)
            assert collector.provider is not None, (
                "memory-provider collector got no provider — register did not "
                "dispatch to the bridge"
            )
            assert type(collector.provider).__name__ == "MnemosyneMemoryProvider", (
                type(collector.provider)
            )
            assert len(hermes_plugin.calls) == 1, (
                "legacy hermes_plugin.register invoked for a memory-provider "
                "collector: " + repr(hermes_plugin.calls)
            )

            # 3. register_memory_provider must exist as a separate symbol
            assert hasattr(mnemosyne, 'register_memory_provider'), (
                "expected mnemosyne.register_memory_provider"
            )
            assert callable(mnemosyne.register_memory_provider), (
                "register_memory_provider is not callable"
            )

            # 4. __all__ must include register (existing contract)
            assert 'register' in mnemosyne.__all__, (
                "register missing from __all__: " + repr(mnemosyne.__all__)
            )

            print("PASS")
        """).format(
            mock_root=str(mock_root),
            repo_root=str(REPO_ROOT),
            repo_parent=str(REPO_ROOT.parent),
            plugin_dir=str(plugin_package),
        )

        result = subprocess.run(
            [sys.executable, "-W", "ignore::DeprecationWarning", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(plugin_parent),
        )

    assert result.returncode == 0, (
        f"subprocess failed (exit {result.returncode}):\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "PASS" in result.stdout, result.stdout
