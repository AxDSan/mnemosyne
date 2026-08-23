"""Sleep dry-run prints trajectory counts; live path consumes records.

Fixture messages are injected — these tests never open ~/.hermes/state.db.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mnemosyne.core import llm_backends
from test_trajectory_normalize import FIXTURE_MESSAGES, SESSION_ID


REPO_ROOT = Path(__file__).resolve().parent.parent

CLI_COPIES = [
    REPO_ROOT / "integrations" / "hermes" / "src" / "mnemosyne_hermes" / "cli.py",
    REPO_ROOT / "hermes_memory_provider" / "cli.py",
]

# Same conversation as FIXTURE_MESSAGES, plus raw Hermes tool XML in content
# so dry-run/live prompts must not leak the XML dump.
FIXTURE_WITH_TOOL_XML = [
    {
        "role": "user",
        "content": "List the repo root.",
        "timestamp": 1_700_000_000.0,
    },
    {
        "role": "assistant",
        "content": (
            "I'll list the directory.\n"
            "<tool_call>\n"
            "terminal\n"
            '{"command": "ls"}\n'
            "</tool_call>"
        ),
        "reasoning_content": "Need a directory listing before answering.",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "terminal",
                    "arguments": '{"command": "ls"}',
                },
            }
        ],
        "timestamp": 1_700_000_001.0,
    },
    {
        "role": "tool",
        "tool_name": "terminal",
        "tool_call_id": "call_1",
        "content": "mnemosyne\ntests",
        "ok": True,
        "timestamp": 1_700_000_002.0,
    },
]

RAW_TOOL_MARKERS = ("<tool_call>", "</tool_call>", "<tool_calls>", "</tool_calls>")


@pytest.fixture
def fake_agent_module(monkeypatch):
    agent_pkg = types.ModuleType("agent")
    aux_client = types.ModuleType("agent.auxiliary_client")
    agent_pkg.auxiliary_client = aux_client
    monkeypatch.setitem(sys.modules, "agent", agent_pkg)
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", aux_client)
    yield aux_client
    llm_backends.set_host_llm_backend(None)


@pytest.fixture(autouse=True)
def _clear_backend_and_injection():
    llm_backends.set_host_llm_backend(None)
    yield
    llm_backends.set_host_llm_backend(None)
    try:
        from mnemosyne import trajectory

        trajectory.set_injected_session_messages(None)
    except Exception:
        pass


def _load_cli_standalone(cli_path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(cli_path))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_from_messages_returns_records_and_counts():
    from mnemosyne.trajectory import from_messages

    records, counts = from_messages(FIXTURE_MESSAGES, session_id=SESSION_ID)
    assert counts["user"] == 1
    assert counts["assistant"] == 1
    assert counts["tool_call"] == 1
    assert counts["tool_result"] == 1
    assert any(record["type"] == "user" for record in records)
    assert any(record["type"] == "tool_call" for record in records)


def test_format_count_line_and_unavailable():
    from mnemosyne.trajectory import format_count_line

    line = format_count_line(
        {"user": 1, "assistant": 1, "tool_call": 1, "tool_result": 1}
    )
    assert line.startswith("trajectory:")
    assert "user=1" in line
    assert "assistant=1" in line
    assert "tool_call=1" in line
    assert "tool_result=1" in line
    assert format_count_line(None) == "trajectory: unavailable"


@pytest.mark.parametrize(
    "cli_path",
    CLI_COPIES,
    ids=[str(p.relative_to(REPO_ROOT)) for p in CLI_COPIES],
)
def test_sleep_dry_run_prints_trajectory_counts_not_tool_xml(
    fake_agent_module, monkeypatch, cli_path
):
    from mnemosyne import trajectory

    call_llm = MagicMock(return_value={"choices": [{"message": {"content": "ok"}}]})
    fake_agent_module.call_llm = call_llm

    trajectory.set_injected_session_messages(FIXTURE_WITH_TOOL_XML)

    mod_name = f"_test_sleep_traj_{cli_path.stem}_{hash(str(cli_path)) & 0xFFFFFFFF:x}"
    mod = _load_cli_standalone(cli_path, mod_name)

    from mnemosyne.core import beam as beam_module

    class FakeBeam:
        def sleep(self, dry_run=False):
            raise AssertionError("beam.sleep must not run on --dry-run")

        def sleep_all_sessions(self, dry_run=False):
            raise AssertionError("beam.sleep_all_sessions must not run on --dry-run")

    monkeypatch.setattr(beam_module, "BeamMemory", lambda *_a, **_k: FakeBeam())

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.mnemosyne_command(
            argparse.Namespace(
                mnemosyne_cmd="sleep",
                dry_run=True,
                all_sessions=False,
                bank=None,
                session="sess-fixture-1",
            )
        )
    assert rc == 0
    out = buf.getvalue()
    assert "sleep aux:" in out
    assert "trajectory:" in out
    assert "user=1" in out
    assert "assistant=1" in out
    assert "tool_call=1" in out
    assert "tool_result=1" in out
    for marker in RAW_TOOL_MARKERS:
        assert marker not in out
    call_llm.assert_not_called()


@pytest.mark.parametrize(
    "cli_path",
    CLI_COPIES,
    ids=[str(p.relative_to(REPO_ROOT)) for p in CLI_COPIES],
)
def test_sleep_dry_run_unavailable_without_messages(
    fake_agent_module, monkeypatch, cli_path, tmp_path
):
    fake_agent_module.call_llm = MagicMock(
        return_value={"choices": [{"message": {"content": "ok"}}]}
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "empty-hermes"))
    mod_name = f"_test_sleep_traj_unavail_{cli_path.stem}_{hash(str(cli_path)) & 0xFFFFFFFF:x}"
    mod = _load_cli_standalone(cli_path, mod_name)

    from mnemosyne.core import beam as beam_module

    class FakeBeam:
        def sleep(self, dry_run=False):
            return {"dry_run": dry_run}

        def sleep_all_sessions(self, dry_run=False):
            return {"dry_run": dry_run}

    monkeypatch.setattr(beam_module, "BeamMemory", lambda *_a, **_k: FakeBeam())

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.mnemosyne_command(
            argparse.Namespace(
                mnemosyne_cmd="sleep",
                dry_run=True,
                all_sessions=False,
                bank=None,
            )
        )
    assert rc == 0
    out = buf.getvalue()
    assert "sleep aux:" in out
    assert "trajectory: unavailable" in out
    fake_agent_module.call_llm.assert_not_called()


def test_sleep_prompt_items_are_compact_json_not_tool_xml():
    from mnemosyne.core.model_refresh import build_model_refresh_prompt
    from mnemosyne.trajectory import from_messages, sleep_prompt_items

    records, _counts = from_messages(FIXTURE_WITH_TOOL_XML, session_id=SESSION_ID)
    items = sleep_prompt_items(records)
    prompt = build_model_refresh_prompt(items)
    for marker in RAW_TOOL_MARKERS:
        assert marker not in prompt
    assert '"type":"user"' in prompt or '"type": "user"' in prompt
    assert "tool_call" in prompt


def test_sleep_model_refresh_uses_trajectory_not_working_memory_xml(tmp_path, monkeypatch):
    """Live sleep feeds trajectory JSON into model-refresh, not WM tool XML."""
    from datetime import datetime, timedelta

    from mnemosyne.core import local_llm
    from mnemosyne.core.beam import BeamMemory
    from mnemosyne.trajectory import from_messages

    db_path = tmp_path / "mnemo.db"
    beam = BeamMemory(session_id="sleep-traj", db_path=db_path)
    old_ts = (datetime.now() - timedelta(hours=200)).isoformat()
    xml = '<tool_call>\nterminal\n{"command": "ls"}\n</tool_call>'
    beam.conn.execute(
        "INSERT INTO working_memory (id, content, source, timestamp, session_id) "
        "VALUES (?, ?, ?, ?, ?)",
        ("wm-xml-1", f"User asked to list files.\n{xml}", "conversation", old_ts, "sleep-traj"),
    )
    beam.conn.commit()

    records, _counts = from_messages(FIXTURE_WITH_TOOL_XML, session_id="sleep-traj")
    beam.sleep_trajectory_records = records

    captured: list[str] = []

    def _host(prompt, **_kwargs):
        captured.append(prompt)
        return True, "[]"

    monkeypatch.setattr(local_llm, "_try_host_llm", _host)
    monkeypatch.setattr(local_llm, "_call_remote_llm", MagicMock())
    monkeypatch.setattr(local_llm, "llm_available", lambda: False)

    result = beam.sleep(dry_run=False)
    assert result["status"] == "consolidated"
    assert captured, "model-refresh should have been invoked"
    prompt = captured[0]
    for marker in RAW_TOOL_MARKERS:
        assert marker not in prompt
    assert xml not in prompt
    assert "tool_call" in prompt
    assert '"type":"user"' in prompt or "List the repo root" in prompt
