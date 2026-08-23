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


def test_schemas_for_mode_filters_explicit_schema_list():
    custom = [
        {"name": "mnemosyne_recall"},
        {"name": "mnemosyne_remember"},
        {"name": "mnemosyne_export"},
    ]
    names = {schema["name"] for schema in schemas_for_mode("none", schemas=custom)}
    assert names == {"mnemosyne_recall"}


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


def test_explicit_empty_tools_wins_over_interactive_writes(tmp_path):
    _write_mnemosyne_block(
        tmp_path,
        (
            "memory:\n"
            "  provider: mnemosyne\n"
            "  mnemosyne:\n"
            "    interactive_writes: full\n"
            "    tools: []\n"
        ),
    )
    provider = MnemosyneMemoryProvider()
    provider.initialize(
        "interactive-writes-empty-tools",
        hermes_home=str(tmp_path),
        agent_context="subagent",
    )
    assert provider._interactive_mode == "full"
    assert provider._configured_tool_schemas() == []
    assert "mnemosyne_recall" not in _schema_names(provider)


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


def _schema_names(provider) -> set[str]:
    return {schema["name"] for schema in provider.get_tool_schemas()}


def test_no_interactive_writes_key_defaults_full_includes_remember(tmp_path):
    _write_mnemosyne_block(
        tmp_path,
        "memory:\n  provider: mnemosyne\n  mnemosyne: {}\n",
    )
    provider = MnemosyneMemoryProvider()
    provider.initialize(
        "interactive-writes-default",
        hermes_home=str(tmp_path),
        agent_context="subagent",
    )
    assert "mnemosyne_remember" in _schema_names(provider)


def test_explicit_none_has_recall_no_remember(tmp_path):
    _write_mnemosyne_block(
        tmp_path,
        "memory:\n  provider: mnemosyne\n  mnemosyne:\n    interactive_writes: none\n",
    )
    provider = MnemosyneMemoryProvider()
    provider.initialize(
        "interactive-writes-none",
        hermes_home=str(tmp_path),
        agent_context="subagent",
    )
    names = _schema_names(provider)
    assert "mnemosyne_recall" in names
    assert "mnemosyne_remember" not in names


def test_explicit_slim_has_remember_no_export_or_graph_link(tmp_path):
    _write_mnemosyne_block(
        tmp_path,
        "memory:\n  provider: mnemosyne\n  mnemosyne:\n    interactive_writes: slim\n",
    )
    provider = MnemosyneMemoryProvider()
    provider.initialize(
        "interactive-writes-slim",
        hermes_home=str(tmp_path),
        agent_context="subagent",
    )
    names = _schema_names(provider)
    assert "mnemosyne_remember" in names
    assert "mnemosyne_export" not in names
    assert "mnemosyne_graph_link" not in names


def test_unknown_interactive_writes_raises(tmp_path):
    _write_mnemosyne_block(
        tmp_path,
        "memory:\n  provider: mnemosyne\n  mnemosyne:\n    interactive_writes: banana\n",
    )
    provider = MnemosyneMemoryProvider()
    with pytest.raises(ValueError, match="interactive_writes"):
        provider.initialize(
            "interactive-writes-unknown",
            hermes_home=str(tmp_path),
            agent_context="subagent",
        )
