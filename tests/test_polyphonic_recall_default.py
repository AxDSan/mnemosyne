"""Tests for the polyphonic_recall provider default.

The linear recall scorer only vector-searches episodic memory ("Uses
sqlite-vec + FTS5 for episodic, FTS5 for working" -- BeamMemory.recall).
Every memory starts in working memory, so until consolidation promotes it,
recall was keyword-only and paraphrased questions returned nothing even when
a stored fact was a strong embedding match. The Hermes provider therefore
enables the polyphonic engine by default, while leaving an explicit
MNEMOSYNE_POLYPHONIC_RECALL env var authoritative.

Both provider paths are covered:

1. hermes_memory_provider — legacy plugin
2. mnemosyne_hermes — pip-installable integration provider
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

ENV_VAR = "MNEMOSYNE_POLYPHONIC_RECALL"


@pytest.fixture(autouse=True)
def _clean_env():
    """Run each test with the gate env var unset, and restore it after."""
    original = os.environ.get(ENV_VAR)
    os.environ.pop(ENV_VAR, None)
    try:
        yield
    finally:
        os.environ.pop(ENV_VAR, None)
        if original is not None:
            os.environ[ENV_VAR] = original


def _provider_classes():
    """Yield (label, class) for every shipped provider implementation."""
    from hermes_memory_provider import MnemosyneMemoryProvider as Legacy

    yield "hermes_memory_provider", Legacy
    try:
        from mnemosyne_hermes import MnemosyneMemoryProvider as Pip
    except ImportError:  # integration package not on sys.path
        return
    yield "mnemosyne_hermes", Pip


PROVIDERS = list(_provider_classes())
PROVIDER_IDS = [label for label, _ in PROVIDERS]
PROVIDER_CLASSES = [cls for _, cls in PROVIDERS]


@pytest.mark.parametrize("provider_cls", PROVIDER_CLASSES, ids=PROVIDER_IDS)
class TestPolyphonicRecallDefault:
    def test_enabled_by_default(self, provider_cls):
        """With no config and no env var, the gate is turned on."""
        provider = provider_cls()
        with patch.object(provider_cls, "_read_config_key", return_value=None):
            provider._apply_provider_config({})
        assert os.environ[ENV_VAR] == "1"

    def test_config_false_disables(self, provider_cls):
        """polyphonic_recall: false in config.yaml restores the linear scorer."""
        provider = provider_cls()
        with patch.object(
            provider_cls,
            "_read_config_key",
            side_effect=lambda key: False if key == "polyphonic_recall" else None,
        ):
            provider._apply_provider_config({})
        assert os.environ[ENV_VAR] == "0"

    def test_kwargs_take_precedence_over_config(self, provider_cls):
        """kwargs beat config.yaml, matching the other provider config keys."""
        provider = provider_cls()
        with patch.object(
            provider_cls,
            "_read_config_key",
            side_effect=lambda key: True if key == "polyphonic_recall" else None,
        ):
            provider._apply_provider_config({"polyphonic_recall": False})
        assert os.environ[ENV_VAR] == "0"

    @pytest.mark.parametrize(
        "raw,expected",
        [("true", "1"), ("ON", "1"), ("Yes", "1"), ("1", "1"),
         ("false", "0"), ("off", "0"), ("no", "0"), ("0", "0")],
    )
    def test_string_values_are_coerced(self, provider_cls, raw, expected):
        """String config values are parsed like the other boolean keys."""
        provider = provider_cls()
        with patch.object(
            provider_cls,
            "_read_config_key",
            side_effect=lambda key: raw if key == "polyphonic_recall" else None,
        ):
            provider._apply_provider_config({})
        assert os.environ[ENV_VAR] == expected

    @pytest.mark.parametrize("preset", ["0", "1"])
    def test_explicit_env_var_wins(self, provider_cls, preset):
        """An operator-set env var is never overwritten by the config default."""
        os.environ[ENV_VAR] = preset
        provider = provider_cls()
        with patch.object(
            provider_cls,
            "_read_config_key",
            side_effect=lambda key: (preset == "0") if key == "polyphonic_recall" else None,
        ):
            provider._apply_provider_config({})
        assert os.environ[ENV_VAR] == preset

    def test_config_schema_advertises_the_key(self, provider_cls):
        """`hermes memory setup` can discover and document the new key."""
        schema = provider_cls().get_config_schema()
        entry = next(
            (field for field in schema if field.get("key") == "polyphonic_recall"),
            None,
        )
        assert entry is not None, "polyphonic_recall missing from get_config_schema()"
        assert entry.get("default") is True
