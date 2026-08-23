"""Sleep dry-run prints trajectory counts; live path consumes records.

Fixture messages are injected — these tests never open ~/.hermes/state.db.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import sqlite3
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


def test_from_messages_empty_is_meta_only_not_session_trajectory():
    """Empty sessions still emit [meta]; that must not count as a trajectory."""
    from mnemosyne.trajectory import from_messages, has_session_trajectory

    records, counts = from_messages([], session_id="empty")
    assert records
    assert all(record["type"] == "meta" for record in records)
    assert counts == {"user": 0, "assistant": 0, "tool_call": 0, "tool_result": 0}
    assert has_session_trajectory(records) is False
    assert has_session_trajectory(None) is False
    assert has_session_trajectory([]) is False


def test_attach_sleep_trajectory_skips_meta_only_and_attaches_real_turns():
    from mnemosyne.trajectory import attach_sleep_trajectory, has_session_trajectory

    empty_beam = types.SimpleNamespace()
    attach_sleep_trajectory(empty_beam, session_id="empty", messages=[])
    assert getattr(empty_beam, "sleep_trajectory_records", None) is None

    live = types.SimpleNamespace()
    attach_sleep_trajectory(live, session_id=SESSION_ID, messages=FIXTURE_MESSAGES)
    assert has_session_trajectory(live.sleep_trajectory_records)


def test_sleep_model_refresh_falls_back_to_wm_when_trajectory_is_meta_only(
    tmp_path, monkeypatch
):
    """Meta-only records must not replace working-memory for model-refresh."""
    from datetime import datetime, timedelta

    from mnemosyne.core import local_llm
    from mnemosyne.core.beam import BeamMemory
    from mnemosyne.trajectory import from_messages

    db_path = tmp_path / "mnemo.db"
    beam = BeamMemory(session_id="sleep-wm", db_path=db_path)
    old_ts = (datetime.now() - timedelta(hours=200)).isoformat()
    wm_fact = "User prefers dark mode in the editor."
    beam.conn.execute(
        "INSERT INTO working_memory (id, content, source, timestamp, session_id) "
        "VALUES (?, ?, ?, ?, ?)",
        ("wm-dark-1", wm_fact, "conversation", old_ts, "sleep-wm"),
    )
    beam.conn.commit()

    records, _counts = from_messages([], session_id="sleep-wm")
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
    assert wm_fact in captured[0]


def _provider_modules():
    integration_src = REPO_ROOT / "integrations" / "hermes" / "src"
    import hermes_memory_provider

    if str(integration_src) not in sys.path:
        sys.path.insert(0, str(integration_src))
    import mnemosyne_hermes

    return (
        ("hermes_memory_provider", hermes_memory_provider),
        ("mnemosyne_hermes", mnemosyne_hermes),
    )


@pytest.mark.parametrize("mod_name,module", _provider_modules())
def test_on_session_end_skips_meta_only_and_attaches_real_turns(monkeypatch, mod_name, module):
    created: list[object] = []

    class WorkerBeam:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created.append(self)

        def sleep(self, *args, **kwargs):
            return {"status": "consolidated"}

    monkeypatch.setattr(module, "_get_beam_class", lambda: WorkerBeam)

    provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
    provider._beam = types.SimpleNamespace(
        session_id="sess-fixture-1",
        db_path="/tmp/test.db",
        author_id="agent_1",
        author_type="hermes",
        channel_id="test:channel",
        canonical_owner_id="default",
        agent_context="primary",
    )
    provider._audit = None
    provider._session_id = "sess-fixture-1"
    provider.SESSION_END_SLEEP_TIMEOUT_SECONDS = 5
    if hasattr(provider, "_reserve_reflection_budget"):
        monkeypatch.setattr(provider, "_reserve_reflection_budget", lambda name: None)
    if hasattr(provider, "_reserve_reflection_budget_locked"):
        monkeypatch.setattr(provider, "_reserve_reflection_budget_locked", lambda name: None)

    created.clear()
    provider.on_session_end(messages=[])
    assert created, f"{mod_name} should create an isolated sleep beam"
    assert getattr(created[0], "sleep_trajectory_records", None) is None

    created.clear()
    provider.on_session_end(messages=FIXTURE_MESSAGES)
    assert created, f"{mod_name} should create an isolated sleep beam"
    from mnemosyne.trajectory import has_session_trajectory

    assert has_session_trajectory(created[0].sleep_trajectory_records)


@pytest.mark.parametrize("mod_name,module", _provider_modules())
def test_handle_sleep_and_auto_sleep_attach_resolved_trajectory(
    monkeypatch, mod_name, module
):
    from mnemosyne import trajectory
    from mnemosyne.trajectory import has_session_trajectory

    trajectory.set_injected_session_messages(FIXTURE_MESSAGES)

    class SourceBeam:
        session_id = "sess-fixture-1"
        db_path = "/tmp/test.db"
        author_id = "agent_1"
        author_type = "hermes"
        channel_id = "test:channel"
        canonical_owner_id = "default"
        agent_context = "primary"

        def get_working_stats(self):
            return {"total": 50}

        def get_episodic_stats(self):
            return {}

        def _count_unconsolidated_before(self, _cutoff):
            return 5

        def sleep(self, dry_run=False, force=False):
            return {"status": "consolidated"}

        def sleep_all_sessions(self, dry_run=False, force=False):
            raise AssertionError("must not sweep all sessions")

    created: list[object] = []

    class WorkerBeam:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created.append(self)

        def sleep(self, *args, **kwargs):
            return {"status": "consolidated"}

    monkeypatch.setattr(module, "_get_beam_class", lambda: WorkerBeam)

    provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
    source = SourceBeam()
    provider._beam = source
    provider._audit = None
    provider._session_id = "sess-fixture-1"
    provider._auto_sleep_threshold = 10
    provider._AUTO_SLEEP_TIMEOUT_SECONDS = 5
    if hasattr(provider, "_reserve_reflection_budget"):
        monkeypatch.setattr(provider, "_reserve_reflection_budget", lambda name: None)
    if hasattr(provider, "_reserve_reflection_budget_locked"):
        monkeypatch.setattr(provider, "_reserve_reflection_budget_locked", lambda name: None)

    provider._handle_sleep({"dry_run": False})
    assert has_session_trajectory(getattr(source, "sleep_trajectory_records", None)), (
        f"{mod_name} _handle_sleep must attach resolved session records"
    )

    created.clear()
    provider._maybe_auto_sleep()
    assert created, f"{mod_name} auto-sleep should create an isolated beam"
    assert has_session_trajectory(getattr(created[0], "sleep_trajectory_records", None)), (
        f"{mod_name} _maybe_auto_sleep must attach resolved session records"
    )


def _write_hermes_state_db(home: Path, sessions: list[tuple], messages: list[dict]) -> None:
    """Create a Hermes-shaped state.db (sessions + messages) under home."""
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(home / "state.db")
    try:
        conn.execute(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL DEFAULT 'cli',
                started_at REAL NOT NULL,
                last_activity_at REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_calls TEXT,
                tool_name TEXT,
                tool_call_id TEXT,
                reasoning TEXT,
                reasoning_content TEXT,
                timestamp REAL NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.executemany(
            "INSERT INTO sessions (id, source, started_at, last_activity_at) "
            "VALUES (?, 'cli', ?, ?)",
            sessions,
        )
        for message in messages:
            conn.execute(
                "INSERT INTO messages ("
                "session_id, role, content, tool_calls, tool_name, tool_call_id, "
                "reasoning, reasoning_content, timestamp, active"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message["session_id"],
                    message["role"],
                    message.get("content"),
                    message.get("tool_calls"),
                    message.get("tool_name"),
                    message.get("tool_call_id"),
                    message.get("reasoning"),
                    message.get("reasoning_content"),
                    message["timestamp"],
                    message.get("active", 1),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def test_attach_sleep_trajectory_maps_hermes_prefix_and_falls_back_to_latest(
    tmp_path, monkeypatch
):
    """Beam session_id is hermes_<raw>; state.db stores the raw Hermes id."""
    from mnemosyne.trajectory import attach_sleep_trajectory, has_session_trajectory

    older_id = "20260822_100000_aaaaaa"
    latest_id = "20260823_120000_bbbbbb"
    hermes_home = tmp_path / "hermes-home"
    _write_hermes_state_db(
        hermes_home,
        sessions=[
            (older_id, 1_700_000_000.0, 1_700_000_100.0),
            (latest_id, 1_700_100_000.0, 1_700_100_200.0),
        ],
        messages=[
            {
                "session_id": older_id,
                "role": "user",
                "content": "older session unique fact",
                "timestamp": 1_700_000_050.0,
            },
            {
                "session_id": latest_id,
                "role": "user",
                "content": "latest session unique fact",
                "timestamp": 1_700_100_050.0,
            },
        ],
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    prefixed = types.SimpleNamespace()
    attach_sleep_trajectory(prefixed, session_id=f"hermes_{older_id}")
    assert has_session_trajectory(getattr(prefixed, "sleep_trajectory_records", None))
    contents = [record.get("content") for record in prefixed.sleep_trajectory_records]
    assert "older session unique fact" in contents
    assert "latest session unique fact" not in contents

    unknown = types.SimpleNamespace()
    attach_sleep_trajectory(unknown, session_id="hermes_no_such_session")
    assert has_session_trajectory(getattr(unknown, "sleep_trajectory_records", None))
    contents = [record.get("content") for record in unknown.sleep_trajectory_records]
    assert "latest session unique fact" in contents
    assert "older session unique fact" not in contents