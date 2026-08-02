"""Tests for the hermes_plugin fallback in hermes_memory_provider.register().

Covers the three behaviours required by review on #581 (issue #578):
- absent hermes_plugin sibling -> debug log, no raise;
- a present-but-broken sibling (missing transitive dependency) -> the
  ModuleNotFoundError propagates instead of being swallowed;
- a sibling whose register() raises -> that error propagates.
"""
import logging
import sys
import textwrap
from unittest.mock import MagicMock

import pytest

from hermes_memory_provider import register


@pytest.fixture
def clean_hermes_plugin(monkeypatch):
    """Ensure no cached hermes_plugin import leaks between tests."""
    monkeypatch.delitem(sys.modules, "hermes_plugin", raising=False)
    yield


def _install_fake_hermes_plugin(monkeypatch, tmp_path, init_py):
    """Create an importable hermes_plugin package in tmp_path."""
    pkg = tmp_path / "hermes_plugin"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(textwrap.dedent(init_py))
    monkeypatch.syspath_prepend(str(tmp_path))


def test_register_absent_hermes_plugin_logs_and_returns(clean_hermes_plugin, caplog):
    """Absent sibling: register() does not raise and logs the expected no-op."""
    ctx = MagicMock()
    with caplog.at_level(logging.DEBUG, logger="hermes_memory_provider"):
        register(ctx)  # must not raise
    assert any(
        "hermes_plugin sibling not importable" in r.message for r in caplog.records
    )


def test_register_broken_transitive_import_propagates(
    clean_hermes_plugin, monkeypatch, tmp_path
):
    """A present sibling with a missing transitive dep must surface, not be hidden."""
    _install_fake_hermes_plugin(
        monkeypatch,
        tmp_path,
        """
        import nonexistent_transitive_xyz  # missing dep -> ModuleNotFoundError
        def register(ctx):
            raise AssertionError("should not reach register()")
        """,
    )
    with pytest.raises(ModuleNotFoundError) as excinfo:
        register(MagicMock())
    assert excinfo.value.name == "nonexistent_transitive_xyz"


def test_register_plugin_register_failure_propagates(
    clean_hermes_plugin, monkeypatch, tmp_path
):
    """A failure inside the sibling's register(ctx) must propagate."""
    _install_fake_hermes_plugin(
        monkeypatch,
        tmp_path,
        """
        def register(ctx):
            raise RuntimeError("plugin registration failed")
        """,
    )
    with pytest.raises(RuntimeError, match="plugin registration failed"):
        register(MagicMock())
