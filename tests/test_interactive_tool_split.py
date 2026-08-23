"""Interactive slim/full/none tool-set split for the Hermes provider."""

from pathlib import Path

import pytest

from hermes_memory_provider import ALL_TOOL_SCHEMAS, MnemosyneMemoryProvider
from hermes_memory_provider.tool_sets import schemas_for_mode


def test_slim_excludes_graph_and_export():
    names = {s["name"] for s in schemas_for_mode("slim")}
    assert "mnemosyne_remember" in names
    assert "mnemosyne_recall" in names
    assert "mnemosyne_graph_link" not in names
    assert "mnemosyne_export" not in names
    assert "mnemosyne_scratchpad_write" not in names


def test_none_is_read_only():
    names = {s["name"] for s in schemas_for_mode("none")}
    assert "mnemosyne_remember" not in names
    assert "mnemosyne_recall" in names


def test_full_superset_of_slim():
    slim = {s["name"] for s in schemas_for_mode("slim")}
    full = {s["name"] for s in schemas_for_mode("full")}
    assert slim <= full


def test_unknown_mode_fails_loudly():
    with pytest.raises(ValueError, match="Unknown interactive tool mode"):
        schemas_for_mode("not-a-mode")


def test_default_interactive_mode_is_full():
    provider = MnemosyneMemoryProvider()
    assert provider._interactive_mode == "full"
    names = {schema["name"] for schema in provider._configured_tool_schemas()}
    assert names == {schema["name"] for schema in ALL_TOOL_SCHEMAS}


def test_initialize_resets_interactive_mode_to_full(tmp_path):
    provider = MnemosyneMemoryProvider()
    provider._interactive_mode = "slim"
    provider.initialize(
        "interactive-mode-default",
        hermes_home=str(tmp_path),
        agent_context="subagent",
    )
    assert provider._interactive_mode == "full"


def _write_mnemosyne_block(hermes_home: Path, body: str) -> None:
    hermes_home.mkdir(parents=True, exist_ok=True)
    (hermes_home / "config.yaml").write_text(body)


def test_explicit_tools_allowlist_wins_over_mode(tmp_path):
    _write_mnemosyne_block(
        tmp_path,
        "memory:\n  provider: mnemosyne\n  mnemosyne:\n    tools: []\n",
    )
    provider = MnemosyneMemoryProvider()
    provider._hermes_home = str(tmp_path)
    provider._interactive_mode = "full"
    assert provider._configured_tool_schemas() == []


def test_omitted_tools_uses_interactive_mode(tmp_path):
    _write_mnemosyne_block(
        tmp_path,
        "memory:\n  provider: mnemosyne\n  mnemosyne: {}\n",
    )
    provider = MnemosyneMemoryProvider()
    provider._hermes_home = str(tmp_path)
    provider._interactive_mode = "slim"
    names = {schema["name"] for schema in provider._configured_tool_schemas()}
    assert names == {schema["name"] for schema in schemas_for_mode("slim")}
