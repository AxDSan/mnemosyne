from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import mnemosyne_hermes
import pytest
from mnemosyne.core.config import MnemosyneConfig
from mnemosyne_hermes import MnemosyneMemoryProvider


def _shutdown_and_close(provider: MnemosyneMemoryProvider) -> None:
    memory = provider._memory
    beam = provider._beam
    connections = []
    for owner in (memory, beam):
        conn = getattr(owner, "conn", None)
        if conn is not None and all(conn is not existing for existing in connections):
            connections.append(conn)

    provider.shutdown()
    for conn in connections:
        conn.close()


class _RecordingBeam:
    def __init__(self, session_id: str = "hermes_SESS-A") -> None:
        self.session_id = session_id
        self.channel_id = session_id
        self.author_id = None
        self.observed_sessions: list[tuple[str, str]] = []

    def recall(self, **_kwargs: Any) -> list[dict[str, Any]]:
        self.observed_sessions.append((self.session_id, self.channel_id))
        return []

    def remember(self, **_kwargs: Any) -> None:
        self.observed_sessions.append((self.session_id, self.channel_id))


class _ObservedRLock:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.waiting = threading.Event()

    def acquire(self, *args: Any, **kwargs: Any) -> bool:
        self.waiting.set()
        return self._lock.acquire(*args, **kwargs)

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> _ObservedRLock:
        self.acquire()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.release()


def _provider_with_recording_beam(
    *,
    session_id: str = "hermes_SESS-A",
    gateway_session_key: str = "",
) -> tuple[MnemosyneMemoryProvider, _RecordingBeam]:
    provider = MnemosyneMemoryProvider()
    beam = _RecordingBeam(session_id)
    provider._beam = beam
    provider._session_id = session_id
    provider._gateway_session_key = gateway_session_key
    provider._agent_context = "primary"
    provider._skip_contexts = set()
    provider._sync_roles = {"user"}
    provider._auto_sleep_enabled = False
    provider._should_filter = lambda _content: False
    provider._capture_identity_signals = lambda _content: None
    return provider, beam


def _call_surface(
    provider: MnemosyneMemoryProvider,
    surface: str,
    session_id: str,
) -> None:
    if surface == "prefetch":
        provider.prefetch("query for active session", session_id=session_id)
    else:
        provider.sync_turn(
            "user text for active session",
            "",
            session_id=session_id,
        )


def test_switch_then_sync_turn_writes_new_session_and_resets_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(MnemosyneConfig, "_instance", None)
    provider = MnemosyneMemoryProvider()
    provider.initialize("SESS-A", hermes_home=str(tmp_path), auto_sleep=False)
    beam = provider._beam
    assert beam is not None
    provider._turn_count = 8
    provider._reflect_calls_this_session = 2

    try:
        provider.on_session_switch(
            "SESS-B",
            parent_session_id="SESS-A",
            reset=True,
            reason="new_session",
        )
        provider.sync_turn(
            "user text after the switch",
            "",
            session_id="SESS-B",
        )

        with sqlite3.connect(beam.db_path) as conn:
            rows = conn.execute(
                "SELECT session_id, channel_id, content FROM working_memory ORDER BY id"
            ).fetchall()

        assert rows == [
            (
                "hermes_SESS-B",
                "hermes_SESS-B",
                "[USER] user text after the switch",
            )
        ]
        assert provider._session_id == "hermes_SESS-B"
        assert beam.session_id == "hermes_SESS-B"
        assert beam.channel_id == "hermes_SESS-B"
        assert provider._turn_count == 1
        assert provider._reflect_calls_this_session == 0
    finally:
        _shutdown_and_close(provider)


@pytest.mark.parametrize("surface", ["prefetch", "sync_turn"])
@pytest.mark.parametrize(
    ("session_id", "expected_session"),
    [("SESS-B", "hermes_SESS-B"), ("", "hermes_SESS-A")],
)
def test_per_call_session_id_scopes_only_the_locked_beam_access(
    surface: str,
    session_id: str,
    expected_session: str,
) -> None:
    provider, beam = _provider_with_recording_beam()

    _call_surface(provider, surface, session_id)

    assert beam.observed_sessions == [(expected_session, expected_session)]
    assert provider._session_id == "hermes_SESS-A"
    assert beam.session_id == "hermes_SESS-A"
    assert beam.channel_id == "hermes_SESS-A"


@pytest.mark.parametrize("surface", ["prefetch", "sync_turn"])
def test_per_call_session_id_cannot_override_gateway_scope(surface: str) -> None:
    provider, beam = _provider_with_recording_beam(
        session_id="hermes_gateway-topic",
        gateway_session_key="gateway-topic",
    )

    _call_surface(provider, surface, "transient-child-session")

    assert beam.observed_sessions == [("hermes_gateway-topic", "hermes_gateway-topic")]
    assert provider._session_id == "hermes_gateway-topic"
    assert beam.session_id == "hermes_gateway-topic"
    assert beam.channel_id == "hermes_gateway-topic"


def test_gateway_scope_survives_compression_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(MnemosyneConfig, "_instance", None)
    provider = MnemosyneMemoryProvider()
    provider.initialize(
        "SESS-A",
        hermes_home=str(tmp_path),
        gateway_session_key="gateway-topic",
        auto_sleep=False,
    )
    beam = provider._beam
    assert beam is not None

    try:
        provider.on_session_switch(
            "compression-child-session",
            parent_session_id="SESS-A",
            reason="compression",
        )

        assert provider._session_id == "hermes_gateway-topic"
        assert beam.session_id == "hermes_gateway-topic"
        assert beam.channel_id == "hermes_gateway-topic"
    finally:
        _shutdown_and_close(provider)


def test_profile_isolation_switch_preserves_bank_db_and_explicit_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(MnemosyneConfig, "_instance", None)
    provider = MnemosyneMemoryProvider()
    provider.initialize(
        "SESS-A",
        hermes_home=str(tmp_path / "profile-home"),
        agent_identity="profile-alpha",
        profile_isolation=True,
        channel_id="caller-channel",
        auto_sleep=False,
    )
    memory = provider._memory
    beam = provider._beam
    assert memory is not None
    assert beam is not None
    original_bank = memory.bank
    original_db_path = memory.db_path

    try:
        provider.on_session_switch("SESS-B")

        assert provider._memory is memory
        assert memory.bank == original_bank
        assert memory.db_path == original_db_path
        assert beam.db_path == original_db_path
        assert provider._session_id == "hermes_SESS-B"
        assert memory.session_id == "hermes_SESS-B"
        assert beam.session_id == "hermes_SESS-B"
        assert memory.channel_id == "caller-channel"
        assert beam.channel_id == "caller-channel"
    finally:
        _shutdown_and_close(provider)


def test_switch_waits_for_inflight_turn_without_mixing_sessions() -> None:
    turn_started = threading.Event()
    release_turn = threading.Event()
    switch_done = threading.Event()
    writes: list[tuple[str, str]] = []

    class _BlockingBeam(_RecordingBeam):
        def remember(self, **_kwargs: Any) -> None:
            before = (self.session_id, self.channel_id)
            turn_started.set()
            assert release_turn.wait(timeout=5)
            after = (self.session_id, self.channel_id)
            assert after == before
            writes.append(after)

    provider, _ = _provider_with_recording_beam()
    beam = _BlockingBeam()
    provider._beam = beam

    turn = threading.Thread(
        target=provider.sync_turn,
        args=("user text before switch", ""),
        kwargs={"session_id": "SESS-A"},
    )
    turn.start()
    assert turn_started.wait(timeout=5)

    switch = threading.Thread(
        target=lambda: (
            provider.on_session_switch("SESS-B", reset=True),
            switch_done.set(),
        )
    )
    switch.start()
    assert not switch_done.wait(timeout=0.1)

    release_turn.set()
    turn.join(timeout=5)
    switch.join(timeout=5)

    assert not turn.is_alive()
    assert not switch.is_alive()
    assert switch_done.is_set()
    assert writes == [("hermes_SESS-A", "hermes_SESS-A")]
    assert provider._session_id == "hermes_SESS-B"
    assert beam.session_id == "hermes_SESS-B"
    assert beam.channel_id == "hermes_SESS-B"
    assert provider._turn_count == 0


def test_switch_waits_for_inflight_tool_write_without_mixing_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_started = threading.Event()
    release_tool = threading.Event()
    switch_done = threading.Event()
    writes: list[tuple[str, str]] = []
    tool_results: list[str] = []

    class _BlockingToolBeam(_RecordingBeam):
        def remember(self, **_kwargs: Any) -> str:
            before = (self.session_id, self.channel_id)
            tool_started.set()
            assert release_tool.wait(timeout=5)
            after = (self.session_id, self.channel_id)
            assert after == before
            writes.append(after)
            return "memory-id"

    provider, _ = _provider_with_recording_beam()
    beam = _BlockingToolBeam()
    provider._beam = beam
    beam_lock = _ObservedRLock()
    provider._beam_access_lock = beam_lock
    provider.has_tool = lambda name: name == "mnemosyne_remember"
    monkeypatch.setattr(mnemosyne_hermes, "_write_approval_enabled", lambda: False)

    tool = threading.Thread(
        target=lambda: tool_results.append(
            provider.handle_tool_call(
                "mnemosyne_remember",
                {"content": "tool write before switch"},
            )
        )
    )
    tool.start()
    assert tool_started.wait(timeout=5)
    beam_lock.waiting.clear()

    switch = threading.Thread(
        target=lambda: (
            provider.on_session_switch("SESS-B"),
            switch_done.set(),
        )
    )
    switch.start()
    try:
        assert beam_lock.waiting.wait(timeout=5)
        assert not switch_done.is_set()
    finally:
        release_tool.set()
        tool.join(timeout=5)
        switch.join(timeout=5)

    assert not tool.is_alive()
    assert not switch.is_alive()
    assert switch_done.is_set()
    assert len(tool_results) == 1
    assert '"status": "stored"' in tool_results[0]
    assert writes == [("hermes_SESS-A", "hermes_SESS-A")]
    assert provider._session_id == "hermes_SESS-B"
    assert beam.session_id == "hermes_SESS-B"
    assert beam.channel_id == "hermes_SESS-B"


def test_failed_init_switches_pending_retry_to_latest_gateway_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class _FailOnceBeam:
        author_id = None

        def __init__(self, **kwargs: Any) -> None:
            calls.append(dict(kwargs))
            if len(calls) == 1:
                raise sqlite3.OperationalError("database is locked")
            self.session_id = kwargs["session_id"]
            self.channel_id = kwargs.get("channel_id") or self.session_id
            self.db_path = kwargs.get("db_path")

    monkeypatch.setattr(mnemosyne_hermes, "_get_beam_class", lambda: _FailOnceBeam)
    provider = MnemosyneMemoryProvider()
    provider.initialize("SESS-A", gateway_session_key="gateway-topic")
    assert provider._retry_init_args is not None

    provider.on_session_switch("SESS-B")
    assert provider._retry_init_args[0] == "SESS-B"
    assert provider._retry_init_args[1]["gateway_session_key"] == "gateway-topic"
    provider._retry_init_at = 0
    provider._maybe_retry_init()

    assert provider._retry_init_args is None
    assert provider._session_id == "hermes_gateway-topic"
    assert provider._beam is not None
    assert provider._beam.session_id == "hermes_gateway-topic"
    assert [call["session_id"] for call in calls] == [
        "hermes_gateway-topic",
        "hermes_gateway-topic",
    ]
    provider.shutdown()


def test_retry_cannot_publish_old_session_after_switch() -> None:
    provider = MnemosyneMemoryProvider()
    provider._retry_init_args = ("SESS-A", {})
    provider._retry_init_at = 0
    init_started = threading.Event()
    release_init = threading.Event()
    switch_done = threading.Event()

    def blocked_initialize(session_id: str, **_kwargs: Any) -> None:
        init_started.set()
        assert release_init.wait(timeout=5)
        provider._beam = _RecordingBeam(f"hermes_{session_id}")
        provider._session_id = f"hermes_{session_id}"

    provider.initialize = blocked_initialize
    retry = threading.Thread(target=provider._maybe_retry_init)
    retry.start()
    assert init_started.wait(timeout=5)

    switch = threading.Thread(
        target=lambda: (
            provider.on_session_switch("SESS-B"),
            switch_done.set(),
        )
    )
    switch.start()
    assert not switch_done.wait(timeout=0.1)

    release_init.set()
    retry.join(timeout=5)
    switch.join(timeout=5)

    assert not retry.is_alive()
    assert not switch.is_alive()
    assert switch_done.is_set()
    assert provider._session_id == "hermes_SESS-B"
    assert provider._beam is not None
    assert provider._beam.session_id == "hermes_SESS-B"
    assert provider._beam.channel_id == "hermes_SESS-B"


def test_explicit_channel_collision_survives_real_initialize_and_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(MnemosyneConfig, "_instance", None)
    provider = MnemosyneMemoryProvider()
    provider.initialize(
        "SESS-A",
        hermes_home=str(tmp_path),
        channel_id="hermes_SESS-A",
        auto_sleep=False,
    )
    beam = provider._beam
    assert beam is not None

    try:
        provider.on_session_switch("SESS-B")

        assert provider._session_id == "hermes_SESS-B"
        assert beam.session_id == "hermes_SESS-B"
        assert beam.channel_id == "hermes_SESS-A"
    finally:
        _shutdown_and_close(provider)


def test_session_end_reservation_and_snapshot_share_switch_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reservation_entered = threading.Event()
    release_reservation = threading.Event()
    worker_started = threading.Event()
    release_worker = threading.Event()
    switch_done = threading.Event()

    class _SourceBeam:
        session_id = "hermes_SESS-A"
        db_path = "memory.db"
        author_id = "author"
        author_type = "agent"
        channel_id = "channel"

    class _SleepBeam:
        calls: list[dict[str, Any]] = []

        def __init__(self, **kwargs: Any) -> None:
            type(self).calls.append(dict(kwargs))

        def sleep(self) -> None:
            worker_started.set()
            assert release_worker.wait(timeout=5)

    provider = MnemosyneMemoryProvider()
    provider._beam = _SourceBeam()
    provider.SESSION_END_SLEEP_TIMEOUT_SECONDS = 1

    def reserve_locked(_reason: str) -> None:
        reservation_entered.set()
        assert release_reservation.wait(timeout=5)

    provider._reserve_reflection_budget_locked = reserve_locked
    monkeypatch.setattr(mnemosyne_hermes, "_get_beam_class", lambda: _SleepBeam)

    session_end = threading.Thread(target=provider.on_session_end, args=([],))
    session_end.start()
    assert reservation_entered.wait(timeout=5)

    switch = threading.Thread(
        target=lambda: (
            provider.on_session_switch("SESS-B"),
            switch_done.set(),
        )
    )
    switch.start()
    assert not switch_done.wait(timeout=0.1)

    release_reservation.set()
    assert worker_started.wait(timeout=5)
    release_worker.set()
    session_end.join(timeout=5)
    switch.join(timeout=5)
    if provider._session_end_thread is not None:
        provider._session_end_thread.join(timeout=5)

    assert not session_end.is_alive()
    assert not switch.is_alive()
    assert switch_done.is_set()
    assert _SleepBeam.calls == [
        {
            "session_id": "hermes_SESS-A",
            "db_path": "memory.db",
            "author_id": "author",
            "author_type": "agent",
            "channel_id": "channel",
        }
    ]


def test_session_end_sleep_blocks_concurrent_sync_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_started = threading.Event()
    release_sleep = threading.Event()
    turn_write_started = threading.Event()
    writes: list[tuple[str, str]] = []

    class _SourceBeam(_RecordingBeam):
        db_path = "memory.db"
        author_id = "author"
        author_type = "agent"

        def remember(self, **_kwargs: Any) -> None:
            turn_write_started.set()
            writes.append((self.session_id, self.channel_id))

    class _SleepBeam:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def sleep(self) -> None:
            sleep_started.set()
            assert release_sleep.wait(timeout=5)

    provider, _ = _provider_with_recording_beam()
    provider._beam = _SourceBeam()
    beam_lock = _ObservedRLock()
    provider._beam_access_lock = beam_lock
    provider.SESSION_END_SLEEP_TIMEOUT_SECONDS = 1
    monkeypatch.setattr(mnemosyne_hermes, "_get_beam_class", lambda: _SleepBeam)

    session_end = threading.Thread(target=provider.on_session_end, args=([],))
    session_end.start()
    assert sleep_started.wait(timeout=5)
    beam_lock.waiting.clear()

    sync_turn = threading.Thread(
        target=provider.sync_turn,
        args=("user write while session-end sleep runs", ""),
    )
    sync_turn.start()
    try:
        assert beam_lock.waiting.wait(timeout=5)
        assert not turn_write_started.is_set()
    finally:
        release_sleep.set()
        session_end.join(timeout=5)
        sync_turn.join(timeout=5)
        if provider._session_end_thread is not None:
            provider._session_end_thread.join(timeout=5)

    assert not session_end.is_alive()
    assert not sync_turn.is_alive()
    assert turn_write_started.is_set()
    assert writes == [("hermes_SESS-A", "hermes_SESS-A")]


def test_auto_sleep_reservation_and_snapshot_share_switch_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reservation_entered = threading.Event()
    release_reservation = threading.Event()
    worker_started = threading.Event()
    release_worker = threading.Event()
    switch_done = threading.Event()
    thread_errors: list[BaseException] = []

    class _SourceBeam:
        session_id = "hermes_SESS-A"
        db_path = "memory.db"
        author_id = "author"
        author_type = "agent"
        channel_id = "channel"

        def get_working_stats(self) -> dict[str, int]:
            return {"total": 2}

        def _count_unconsolidated_before(self, _cutoff: str) -> int:
            return 1

        def sleep(self) -> None:
            raise AssertionError("auto-sleep must use a worker-local Beam")

        def sleep_all_sessions(self) -> None:
            raise AssertionError("auto-sleep must use a worker-local Beam")

    class _WorkerBeam:
        calls: list[dict[str, Any]] = []

        def __init__(self, **kwargs: Any) -> None:
            type(self).calls.append(dict(kwargs))

        def sleep(self) -> None:
            worker_started.set()
            assert release_worker.wait(timeout=5)

        def sleep_all_sessions(self) -> None:
            worker_started.set()
            assert release_worker.wait(timeout=5)

    provider = MnemosyneMemoryProvider()
    provider._beam = _SourceBeam()
    provider._auto_sleep_threshold = 1
    provider._auto_sleep_enabled = True
    provider._AUTO_SLEEP_TIMEOUT_SECONDS = 1

    def reserve_locked(_reason: str) -> None:
        reservation_entered.set()
        assert release_reservation.wait(timeout=5)

    provider._reserve_reflection_budget_locked = reserve_locked
    monkeypatch.setattr(mnemosyne_hermes, "_get_beam_class", lambda: _WorkerBeam)

    def run_auto_sleep() -> None:
        try:
            provider._maybe_auto_sleep()
        except BaseException as exc:
            thread_errors.append(exc)

    auto_sleep = threading.Thread(target=run_auto_sleep)
    auto_sleep.start()
    assert reservation_entered.wait(timeout=5), thread_errors
    assert not thread_errors

    switch = threading.Thread(
        target=lambda: (
            provider.on_session_switch("SESS-B"),
            switch_done.set(),
        )
    )
    switch.start()
    assert not switch_done.wait(timeout=0.1)

    release_reservation.set()
    assert worker_started.wait(timeout=5)
    release_worker.set()
    auto_sleep.join(timeout=5)
    switch.join(timeout=5)

    assert not auto_sleep.is_alive()
    assert not switch.is_alive()
    assert not thread_errors
    assert switch_done.is_set()
    assert _WorkerBeam.calls == [
        {
            "session_id": "hermes_SESS-A",
            "db_path": "memory.db",
            "author_id": "author",
            "author_type": "agent",
            "channel_id": "channel",
        }
    ]


def test_reinitialize_closes_existing_audit_log() -> None:
    provider = MnemosyneMemoryProvider()
    audit = Mock()
    provider._audit = audit

    provider.initialize("SESS-B", agent_context="subagent")

    audit.close.assert_called_once_with()
    assert provider._audit is None


def test_shutdown_closes_existing_audit_log() -> None:
    provider = MnemosyneMemoryProvider()
    audit = Mock()
    provider._audit = audit

    provider.shutdown()

    audit.close.assert_called_once_with()
    assert provider._audit is None
