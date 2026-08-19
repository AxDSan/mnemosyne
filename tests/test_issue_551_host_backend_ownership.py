"""Regression coverage for #551 host-backend provider ownership."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mnemosyne.core.llm_backends import get_host_llm_backend, set_host_llm_backend


INTEGRATION_SRC = Path(__file__).resolve().parents[1] / "integrations" / "hermes" / "src"


@pytest.fixture
def provider_class():
    sys.path.insert(0, str(INTEGRATION_SRC))
    try:
        from mnemosyne_hermes import MnemosyneMemoryProvider

        yield MnemosyneMemoryProvider
    finally:
        sys.path.remove(str(INTEGRATION_SRC))


@pytest.mark.parametrize("first_to_shutdown", (0, 1))
def test_primary_peer_shutdown_keeps_host_backend_until_final_owner(
    tmp_path, provider_class, first_to_shutdown
):
    """A live primary peer retains the global host backend after its peer exits."""
    providers = [provider_class(), provider_class()]
    try:
        for index, provider in enumerate(providers):
            provider.initialize(
                f"session-{index}",
                hermes_home=str(tmp_path / f"hermes-{index}"),
                agent_context="primary",
            )
            assert provider._beam is not None

        assert get_host_llm_backend() is not None

        providers[first_to_shutdown].shutdown()
        assert get_host_llm_backend() is not None

        providers[1 - first_to_shutdown].shutdown()
        assert get_host_llm_backend() is None
    finally:
        for provider in providers:
            provider.shutdown()
        set_host_llm_backend(None)
