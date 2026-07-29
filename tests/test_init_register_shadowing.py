"""[#565] Regression test: register_memory_provider must not shadow hermes_plugin.register.

When the repo top-level __init__.py is loaded in a Hermes plugin environment
(hermes_plugin is importable), the existing `from hermes_plugin import register`
binds `register` at module scope. If a subsequent `def register(ctx)` or any
other symbol shadows that name, the Hermes plugin registration path silently
breaks — `__all__` still advertises `register` but it points at the wrong
function.

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
    """Create a mock hermes_plugin package."""
    pkg = tmpdir / "hermes_plugin"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        textwrap.dedent("""\
            def register(ctx):
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
            import sys
            sys.path.insert(0, {mock_root!r})
            sys.path.insert(0, {plugin_parent!r})
            blocked = {{{repo_root!r}, {repo_parent!r}}}
            sys.path = [p for p in sys.path if p not in blocked]

            import mnemosyne

            # 1. register must be the hermes_plugin-originated callable
            assert hasattr(mnemosyne, 'register'), (
                "expected mnemosyne.register when hermes_plugin is importable"
            )
            result = mnemosyne.register("dummy_ctx")
            assert result == "hermes_plugin_register_called", (
                "mnemosyne.register returned " + repr(result) + ", "
                "expected 'hermes_plugin_register_called' - "
                "register was shadowed"
            )

            # 2. register_memory_provider must exist as a separate symbol
            assert hasattr(mnemosyne, 'register_memory_provider'), (
                "expected mnemosyne.register_memory_provider"
            )
            assert callable(mnemosyne.register_memory_provider), (
                "register_memory_provider is not callable"
            )

            # 3. __all__ must include register (existing contract)
            assert 'register' in mnemosyne.__all__, (
                "register missing from __all__: " + repr(mnemosyne.__all__)
            )

            print("PASS")
        """).format(
            mock_root=str(mock_root),
            plugin_parent=str(plugin_parent),
            repo_root=str(REPO_ROOT),
            repo_parent=str(REPO_ROOT.parent),
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


def test_register_memory_provider_bridge_delegates():
    """register_memory_provider delegates without breaking register."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        mock_root = _make_mock_hermes_plugin(tmp / "mock_packages")

        plugin_parent = tmp / "plugin_parent"
        plugin_parent.mkdir()
        plugin_package = plugin_parent / "mnemosyne"
        plugin_package.symlink_to(REPO_ROOT, target_is_directory=True)

        script = textwrap.dedent("""\
            import sys
            sys.path.insert(0, {mock_root!r})
            sys.path.insert(0, {plugin_parent!r})
            blocked = {{{repo_root!r}, {repo_parent!r}}}
            sys.path = [p for p in sys.path if p not in blocked]

            import mnemosyne

            # register_memory_provider should be callable
            try:
                mnemosyne.register_memory_provider("test_ctx")
            except ModuleNotFoundError as exc:
                pkg = getattr(mnemosyne, '__package__', '')
                expected_name = f"{{pkg}}.hermes_memory_provider"
                if exc.name == expected_name:
                    print("BRIDGE_ABSENT_OK")
                else:
                    raise
            else:
                print("BRIDGE_CALLED_OK")

            # register is still the hermes_plugin one
            assert mnemosyne.register("x") == "hermes_plugin_register_called"

            print("PASS")
        """).format(
            mock_root=str(mock_root),
            plugin_parent=str(plugin_parent),
            repo_root=str(REPO_ROOT),
            repo_parent=str(REPO_ROOT.parent),
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
