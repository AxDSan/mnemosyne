"""Tests for MnemosyneMemoryProvider host-LLM lifecycle hooks.

Covers decisions A6 (bounded on_session_end), C7 (shutdown unregisters
the host backend), and the registration flow added to initialize().
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from hermes_memory_provider import MnemosyneMemoryProvider
from mnemosyne.core.llm_backends import get_host_llm_backend


# ---------------------------------------------------------------------------
# initialize() registration
# ---------------------------------------------------------------------------

def test_initialize_registers_host_llm_when_register_returns_true(monkeypatch):
    provider = MnemosyneMemoryProvider()
    # Stub BeamMemory so we don't touch the filesystem.
    monkeypatch.setattr("hermes_memory_provider._get_beam_class", lambda: lambda **kwargs: MagicMock())
    # Stub the registration call so the test does not depend on the real
    # adapter behavior — we only verify the hook is invoked and survives.
    with patch("hermes_memory_provider.hermes_llm_adapter.register_hermes_host_llm", return_value=True) as mock_reg:
        provider.initialize(session_id="test-session")
    mock_reg.assert_called_once()


def test_initialize_does_not_fail_when_register_raises(monkeypatch):
    provider = MnemosyneMemoryProvider()
    monkeypatch.setattr("hermes_memory_provider._get_beam_class", lambda: lambda **kwargs: MagicMock())
    with patch(
        "hermes_memory_provider.hermes_llm_adapter.register_hermes_host_llm",
        side_effect=RuntimeError("boom"),
    ):
        # Must not raise.
        provider.initialize(session_id="test-session")
    # initialize() is allowed to leave _beam set even when registration explodes.
    assert provider._beam is not None


def test_initialize_does_not_fail_when_register_returns_false(monkeypatch):
    provider = MnemosyneMemoryProvider()
    monkeypatch.setattr("hermes_memory_provider._get_beam_class", lambda: lambda **kwargs: MagicMock())
    with patch("hermes_memory_provider.hermes_llm_adapter.register_hermes_host_llm", return_value=False):
        provider.initialize(session_id="test-session")
    assert provider._beam is not None


def test_initialize_skips_for_non_primary_context(monkeypatch):
    """REGRESSION: subagent/cron/flush contexts still skip initialization entirely."""
    provider = MnemosyneMemoryProvider()
    with patch("hermes_memory_provider.hermes_llm_adapter.register_hermes_host_llm") as mock_reg:
        provider.initialize(session_id="x", agent_context="cron")
    mock_reg.assert_not_called()
    assert provider._beam is None


# ---------------------------------------------------------------------------
# shutdown() unregistration (decision C7)
# ---------------------------------------------------------------------------

def test_shutdown_clears_host_backend(monkeypatch):
    """After shutdown(), the host LLM backend must be unregistered."""
    from hermes_memory_provider import hermes_llm_adapter

    provider = MnemosyneMemoryProvider()
    # Manually register to simulate a live session.
    hermes_llm_adapter.register_hermes_host_llm()
    assert get_host_llm_backend() is not None

    provider.shutdown()
    assert get_host_llm_backend() is None
    assert provider._beam is None


def test_shutdown_swallows_unregister_failure(monkeypatch):
    """If unregistering raises, shutdown() must still complete."""
    provider = MnemosyneMemoryProvider()
    with patch(
        "hermes_memory_provider.hermes_llm_adapter.unregister_hermes_host_llm",
        side_effect=RuntimeError("boom"),
    ):
        provider.shutdown()  # must not raise
    assert provider._beam is None


# ---------------------------------------------------------------------------
# on_session_end() bounded daemon thread (decision A6)
# ---------------------------------------------------------------------------

def _make_provider_with_blocking_sleep(sleep_duration: float, timeout: float = 0.5):
    """Build a provider whose _beam.sleep() blocks for `sleep_duration` seconds.

    The provider's join timeout is shortened to keep the test suite fast.
    """
    beam = MagicMock()
    beam.sleep.side_effect = lambda: time.sleep(sleep_duration)
    provider = MnemosyneMemoryProvider()
    provider._beam = beam
    provider.SESSION_END_SLEEP_TIMEOUT_SECONDS = timeout
    return provider, beam


def test_on_session_end_returns_within_timeout_when_sleep_blocks():
    """A6 contract: blocking sleep must not block on_session_end past the join cap."""
    # Production timeout is 15s; test uses 0.5s for speed and a 5s outer ceiling.
    provider, beam = _make_provider_with_blocking_sleep(sleep_duration=5.0, timeout=0.5)

    start = time.monotonic()
    provider.on_session_end(messages=[])
    elapsed = time.monotonic() - start

    # 0.5s join cap + slack. A regression making on_session_end synchronous
    # would take ~5s here.
    assert elapsed < 2.0, f"on_session_end took {elapsed:.2f}s, expected <2s"
    beam.sleep.assert_called_once()


def test_on_session_end_logs_warning_on_timeout(caplog):
    provider, _ = _make_provider_with_blocking_sleep(sleep_duration=5.0, timeout=0.5)
    with caplog.at_level("WARNING", logger="hermes_memory_provider"):
        provider.on_session_end(messages=[])
    msgs = [r.getMessage() for r in caplog.records]
    assert any("timed out" in m for m in msgs), msgs


def test_session_end_timeout_default_matches_design():
    """The production default should remain 15s (decision A6)."""
    assert MnemosyneMemoryProvider.SESSION_END_SLEEP_TIMEOUT_SECONDS == 15


def test_on_session_end_completes_when_sleep_is_fast():
    """Fast sleep must be allowed to finish; no warning emitted."""
    beam = MagicMock()
    # No-op sleep returns immediately.
    beam.sleep.return_value = None
    provider = MnemosyneMemoryProvider()
    provider._beam = beam

    provider.on_session_end(messages=[])
    beam.sleep.assert_called_once()


def test_on_session_end_no_op_without_beam():
    """REGRESSION: on_session_end skips work entirely when not initialized."""
    provider = MnemosyneMemoryProvider()
    provider._beam = None
    # Must not raise.
    provider.on_session_end(messages=[])
