"""Parity checks for the two Hermes Mnemosyne provider implementations."""

from __future__ import annotations

import importlib
import json
import sys
import threading
import types
from pathlib import Path

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_SRC = PROJECT_ROOT / "integrations" / "hermes" / "src"


def _drop_modules(prefix: str) -> None:
    for name in list(sys.modules):
        if name == prefix or name.startswith(f"{prefix}."):
            del sys.modules[name]


def _import_module(package: str, import_root: Path):
    _drop_modules(package)
    saved_mnemosyne_modules = {
        name: module for name, module in sys.modules.items()
        if name == "mnemosyne" or name.startswith("mnemosyne.")
    }
    _drop_modules("mnemosyne")
    inserted = [str(import_root)]
    if import_root != PROJECT_ROOT:
        inserted.append(str(PROJECT_ROOT))
    for path in reversed(inserted):
        sys.path.insert(0, path)
    try:
        return importlib.import_module(package)
    finally:
        for path in inserted:
            try:
                sys.path.remove(path)
            except ValueError:
                pass
        for name in list(sys.modules):
            if name == "mnemosyne" or name.startswith("mnemosyne."):
                sys.modules.pop(name, None)
        sys.modules.update(saved_mnemosyne_modules)


@pytest.fixture(scope="module")
def provider_modules():
    return {
        "hermes_memory_provider": _import_module("hermes_memory_provider", PROJECT_ROOT),
        "mnemosyne_hermes": _import_module("mnemosyne_hermes", INTEGRATION_SRC),
    }


@pytest.fixture(scope="module")
def sync_modules():
    return {
        "hermes_memory_provider": _import_module("hermes_memory_provider.sync_adapter", PROJECT_ROOT),
        "mnemosyne_hermes": _import_module("mnemosyne_hermes.sync_adapter", INTEGRATION_SRC),
    }


def _tool_schemas(module):
    return {schema["name"]: schema for schema in module.ALL_TOOL_SCHEMAS}


def _config_schema(module):
    provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
    return {entry["key"]: entry for entry in provider.get_config_schema()}


def _write_mnemosyne_config(hermes_home: Path, tools) -> None:
    if tools is None:
        body = "memory:\n  provider: mnemosyne\n  mnemosyne: {}\n"
    else:
        rendered_tools = "\n".join(f"      - {tool}" for tool in tools)
        body = (
            "memory:\n"
            "  provider: mnemosyne\n"
            "  mnemosyne:\n"
            "    tools:\n"
            f"{rendered_tools}\n"
        )
    (hermes_home / "config.yaml").write_text(body)


def _schema_names(provider) -> list[str]:
    return [schema["name"] for schema in provider.get_tool_schemas()]


def _provider_for_config(module, hermes_home: Path):
    provider = module.MnemosyneMemoryProvider()
    provider._hermes_home = str(hermes_home)
    return provider


def _json_stable(value):
    return json.loads(json.dumps(value, sort_keys=True))


def test_provider_tool_sets_match(provider_modules):
    tool_sets = {name: set(_tool_schemas(module)) for name, module in provider_modules.items()}

    assert tool_sets["hermes_memory_provider"] == tool_sets["mnemosyne_hermes"]
    assert "mnemosyne_sync_push" in tool_sets["hermes_memory_provider"]
    assert "mnemosyne_persona_list" in tool_sets["hermes_memory_provider"]
    assert "mnemosyne_triple_end" in tool_sets["hermes_memory_provider"]


def test_provider_tool_schemas_match(provider_modules):
    root_tools = _tool_schemas(provider_modules["hermes_memory_provider"])
    integration_tools = _tool_schemas(provider_modules["mnemosyne_hermes"])

    assert _json_stable(root_tools) == _json_stable(integration_tools)


def test_provider_config_defaults_match(provider_modules):
    root_config = _config_schema(provider_modules["hermes_memory_provider"])
    integration_config = _config_schema(provider_modules["mnemosyne_hermes"])

    assert _json_stable(root_config) == _json_stable(integration_config)
    assert root_config["auto_sleep"]["default"] is True
    assert root_config["sync_roles"]["default"] == ["user"]
    assert root_config["default_scope"]["choices"] == ["session", "global"]
    assert root_config["default_scope"]["default"] == "session"
    assert root_config["tools"]["default"] is None
    assert root_config["prefetch_cache_size"]["default"] == 50


def test_auto_sleep_runtime_default_enabled(monkeypatch, provider_modules):
    monkeypatch.delenv("MNEMOSYNE_AUTO_SLEEP_ENABLED", raising=False)

    for module in provider_modules.values():
        provider = module.MnemosyneMemoryProvider()
        assert provider._auto_sleep_enabled is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_auto_sleep_env_can_disable_default(monkeypatch, provider_modules, value):
    monkeypatch.setenv("MNEMOSYNE_AUTO_SLEEP_ENABLED", value)

    for module in provider_modules.values():
        provider = module.MnemosyneMemoryProvider()
        assert provider._auto_sleep_enabled is False


@pytest.mark.parametrize("configured", [False, "false", 0])
def test_auto_sleep_config_can_disable_default(tmp_path, monkeypatch, provider_modules, configured):
    monkeypatch.delenv("MNEMOSYNE_AUTO_SLEEP_ENABLED", raising=False)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"memory": {"provider": "mnemosyne", "mnemosyne": {"auto_sleep": configured}}})
    )

    for module in provider_modules.values():
        provider = _provider_for_config(module, tmp_path)
        provider._apply_provider_config({})
        assert provider._auto_sleep_enabled is False


@pytest.mark.parametrize(
    ("env_value", "config_value", "kwarg_value", "expected"),
    [
        ("0", False, True, True),
        ("1", True, False, False),
        ("0", False, "true", True),
        ("1", True, "false", False),
    ],
)
def test_auto_sleep_kwargs_have_highest_precedence(
    tmp_path, monkeypatch, provider_modules, env_value, config_value, kwarg_value, expected
):
    monkeypatch.setenv("MNEMOSYNE_AUTO_SLEEP_ENABLED", env_value)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"memory": {"provider": "mnemosyne", "mnemosyne": {"auto_sleep": config_value}}})
    )

    for module in provider_modules.values():
        provider = _provider_for_config(module, tmp_path)
        provider._apply_provider_config({"auto_sleep": kwarg_value})
        assert provider._auto_sleep_enabled is expected


def test_save_config_persists_auto_sleep_default_when_missing(tmp_path, provider_modules):
    (tmp_path / "config.yaml").write_text(
        "memory:\n"
        "  provider: mnemosyne\n"
        "  mnemosyne:\n"
        "    sleep_threshold: 75\n"
    )

    for name, module in provider_modules.items():
        hermes_home = tmp_path / name
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text((tmp_path / "config.yaml").read_text())

        provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
        provider.save_config({}, str(hermes_home))

        cfg = yaml.safe_load((hermes_home / "config.yaml").read_text())
        mnemosyne_cfg = cfg["memory"]["mnemosyne"]
        assert mnemosyne_cfg["auto_sleep"] is True
        assert mnemosyne_cfg["sleep_threshold"] == 75


def test_save_config_respects_auto_sleep_env_opt_out(tmp_path, monkeypatch, provider_modules):
    monkeypatch.setenv("MNEMOSYNE_AUTO_SLEEP_ENABLED", "0")

    for name, module in provider_modules.items():
        hermes_home = tmp_path / name
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text(
            "memory:\n"
            "  provider: mnemosyne\n"
            "  mnemosyne:\n"
            "    sleep_threshold: 75\n"
        )

        provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
        provider.save_config({}, str(hermes_home))

        cfg = yaml.safe_load((hermes_home / "config.yaml").read_text())
        mnemosyne_cfg = cfg["memory"]["mnemosyne"]
        assert mnemosyne_cfg["auto_sleep"] is False
        assert mnemosyne_cfg["sleep_threshold"] == 75


def test_save_config_preserves_explicit_auto_sleep_false(tmp_path, provider_modules):
    for name, module in provider_modules.items():
        hermes_home = tmp_path / name
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text(
            "memory:\n"
            "  provider: mnemosyne\n"
            "  mnemosyne:\n"
            "    auto_sleep: false\n"
        )

        provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
        provider.save_config({}, str(hermes_home))

        cfg = yaml.safe_load((hermes_home / "config.yaml").read_text())
        assert cfg["memory"]["mnemosyne"]["auto_sleep"] is False


def test_tool_whitelist_omitted_exposes_all_tools(tmp_path, provider_modules):
    _write_mnemosyne_config(tmp_path, None)

    observed = {}
    for name, module in provider_modules.items():
        provider = _provider_for_config(module, tmp_path)
        observed[name] = _schema_names(provider)

    all_tools = list(_tool_schemas(provider_modules["hermes_memory_provider"]))
    assert observed["hermes_memory_provider"] == all_tools
    assert observed["mnemosyne_hermes"] == all_tools


def test_tool_whitelist_filters_schemas_before_routing(tmp_path, provider_modules):
    allowed = ["mnemosyne_remember", "mnemosyne_recall", "mnemosyne_sleep"]
    _write_mnemosyne_config(tmp_path, allowed)

    observed = {}
    for name, module in provider_modules.items():
        provider = _provider_for_config(module, tmp_path)
        observed[name] = _schema_names(provider)
        assert provider.has_tool("mnemosyne_remember") is True
        assert provider.has_tool("mnemosyne_forget") is False
        assert provider.has_tool("mnemosyne_batch") is False
        rejected = json.loads(provider.handle_tool_call("mnemosyne_forget", {"memory_id": "x"}))
        assert rejected == {"error": "Unknown Mnemosyne tool: mnemosyne_forget"}
        rejected_batch = json.loads(provider.handle_tool_call("mnemosyne_batch", {"operations": []}))
        assert rejected_batch == {"error": "Unknown Mnemosyne tool: mnemosyne_batch"}

    assert observed["hermes_memory_provider"] == allowed
    assert observed["mnemosyne_hermes"] == allowed
    assert "mnemosyne_forget" not in observed["hermes_memory_provider"]
    # Hermes builds its tool routing map from exposed schemas; filtered-out
    # names must therefore be absent from that registration surface.
    assert "mnemosyne_forget" not in set(observed["mnemosyne_hermes"])


def test_tool_whitelist_empty_list_exposes_no_tools(tmp_path, provider_modules):
    (tmp_path / "config.yaml").write_text(
        "memory:\n"
        "  provider: mnemosyne\n"
        "  mnemosyne:\n"
        "    tools: []\n"
    )

    for module in provider_modules.values():
        provider = _provider_for_config(module, tmp_path)
        assert provider.get_tool_schemas() == []


def test_tool_whitelist_unknown_name_fails_loudly(tmp_path, provider_modules):
    _write_mnemosyne_config(tmp_path, ["mnemosyne_remember", "mnemosyne_not_real"])

    for module in provider_modules.values():
        provider = _provider_for_config(module, tmp_path)
        with pytest.raises(ValueError, match="Unknown Mnemosyne tool.*mnemosyne_not_real"):
            provider.get_tool_schemas()


def test_config_reader_tolerates_null_and_non_mapping_levels(tmp_path):
    from mnemosyne.hermes_config import read_hermes_config_key

    cases = [
        "memory:\n",
        "memory: []\n",
        "memory:\n  mnemosyne:\n",
        "memory:\n  mnemosyne: []\n",
        "[]\n",
    ]
    for index, body in enumerate(cases):
        hermes_home = tmp_path / f"case-{index}"
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text(body)
        assert read_hermes_config_key(str(hermes_home), "tools") is None


@pytest.mark.parametrize(
    ("env_name", "helper_name", "default", "custom"),
    [
        ("MNEMOSYNE_SYNC_TURN_USER_LIMIT", "_sync_turn_user_limit", 500, 123),
        ("MNEMOSYNE_SYNC_TURN_ASSISTANT_LIMIT", "_sync_turn_assistant_limit", 800, 234),
    ],
)
def test_provider_sync_limit_helpers_match(monkeypatch, provider_modules, env_name, helper_name, default, custom):
    monkeypatch.delenv(env_name, raising=False)
    assert {name: getattr(module, helper_name)() for name, module in provider_modules.items()} == {
        "hermes_memory_provider": default,
        "mnemosyne_hermes": default,
    }

    monkeypatch.setenv(env_name, str(custom))
    assert {name: getattr(module, helper_name)() for name, module in provider_modules.items()} == {
        "hermes_memory_provider": custom,
        "mnemosyne_hermes": custom,
    }

    monkeypatch.setenv(env_name, "-10")
    assert {name: getattr(module, helper_name)() for name, module in provider_modules.items()} == {
        "hermes_memory_provider": 0,
        "mnemosyne_hermes": 0,
    }

    monkeypatch.setenv(env_name, "not-an-int")
    assert {name: getattr(module, helper_name)() for name, module in provider_modules.items()} == {
        "hermes_memory_provider": default,
        "mnemosyne_hermes": default,
    }


class _FakeBeam:
    def __init__(self):
        self.calls = []

    def remember(self, **kwargs):
        self.calls.append(kwargs)


def _new_provider(module, *, scope="session", roles=("user", "assistant")):
    provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
    provider._beam = _FakeBeam()
    provider._agent_context = ""
    provider._skip_contexts = set()
    provider._sync_roles = set(roles)
    provider._default_scope = scope
    provider._should_filter = lambda _content: False
    provider._capture_identity_signals = lambda _content: None
    provider._turn_count = 0
    provider._auto_sleep_enabled = False
    provider._audit_event = lambda *args, **kwargs: None
    return provider


@pytest.mark.parametrize(
    ("profile_isolation", "has_active_beam", "args", "expected_kwargs"),
    [
        (
            True,
            True,
            {"repair_vec_working": True, "dry_run": True},
            {
                "repair_vec_working": True,
                "dry_run": True,
                "bank": "isolated-profile",
            },
        ),
        (
            True,
            True,
            {},
            {
                "repair_vec_working": False,
                "dry_run": False,
                "bank": "isolated-profile",
            },
        ),
        (False, True, {}, {"repair_vec_working": False, "dry_run": False}),
        (True, False, {}, {"repair_vec_working": False, "dry_run": False}),
    ],
)
def test_provider_diagnose_forwards_options_and_routes_only_active_isolated_bank(
    monkeypatch, provider_modules, profile_isolation, has_active_beam, args, expected_kwargs
):
    """Both shipped providers must preserve diagnose options and active-bank routing."""
    calls = []

    def fake_run_diagnostics(**kwargs):
        calls.append(kwargs)
        return {"checks_total": 0, "key_findings": [], "entries": []}

    monkeypatch.setattr("mnemosyne.diagnose.run_diagnostics", fake_run_diagnostics)

    observed = {}
    for name, module in provider_modules.items():
        provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
        provider._beam = object() if has_active_beam else None
        provider._profile_isolation_enabled = profile_isolation
        provider._sync_turn_diagnostics = lambda: {}

        def resolve_profile_bank():
            if not (profile_isolation and has_active_beam):
                pytest.fail("inactive or non-isolated diagnostics must not resolve a named bank")
            return "isolated-profile"

        provider._resolve_profile_bank = resolve_profile_bank
        json.loads(provider._handle_diagnose(args))
        observed[name] = calls[-1]

    assert len(calls) == len(provider_modules)
    assert observed == {name: expected_kwargs for name in provider_modules}


class _ObservedLock:
    """A real lock with a deterministic signal when a worker tries to enter it."""

    def __init__(self):
        self._lock = threading.Lock()
        self.waiting = threading.Event()

    def acquire(self, *args, **kwargs):
        self.waiting.set()
        return self._lock.acquire(*args, **kwargs)

    def release(self):
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


def test_provider_lazy_beam_lock_initialization_is_thread_safe(monkeypatch, provider_modules):
    """Concurrent __new__ callers publish and receive one lock in both providers."""
    real_lock = threading.Lock

    for module in provider_modules.values():
        module_threading = module.threading
        provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
        creation_barrier = threading.Barrier(2)
        created = []
        returned = []
        failures = []

        def racing_lock():
            # Each worker has already observed a missing lock before either
            # candidate can be constructed. This makes the old check-then-set
            # implementation reliably install and return separate locks.
            creation_barrier.wait(timeout=1)
            candidate = real_lock()
            created.append(candidate)
            return candidate

        def get_lock():
            try:
                returned.append(provider._ensure_beam_access_lock())
            except BaseException as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        workers = [threading.Thread(target=get_lock) for _ in range(2)]
        # `threading` is a shared stdlib module, so replace only this provider
        # module's binding rather than patching threading.Lock process-wide.
        monkeypatch.setattr(module, "threading", types.SimpleNamespace(Lock=racing_lock))
        try:
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=1)
        finally:
            monkeypatch.setattr(module, "threading", module_threading)

        assert not any(worker.is_alive() for worker in workers)
        assert failures == []
        assert len(created) == 2
        assert len(returned) == 2
        assert returned[0] is returned[1]
        assert provider._beam_access_lock is returned[0]


def test_provider_diagnose_waits_for_held_active_beam_lock(monkeypatch, provider_modules):
    """Both providers serialize active diagnostics with auto-sleep Beam access."""
    started = threading.Event()

    def fake_run_diagnostics(**_kwargs):
        started.set()
        return {"checks_total": 0, "key_findings": [], "entries": []}

    monkeypatch.setattr("mnemosyne.diagnose.run_diagnostics", fake_run_diagnostics)

    for module in provider_modules.values():
        provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
        provider._beam = type("Beam", (), {"db_path": None})()
        provider._profile_isolation_enabled = False
        provider._sync_turn_diagnostics = lambda: {}
        lock = _ObservedLock()
        provider._beam_access_lock = lock
        lock.acquire()
        lock.waiting.clear()
        result = []
        worker = threading.Thread(target=lambda: result.append(provider._handle_diagnose({})))
        try:
            worker.start()
            assert lock.waiting.wait(timeout=1), "diagnostics did not attempt the active Beam lock"
            assert not started.is_set(), "diagnostics ran while Beam access was held"
        finally:
            lock.release()
            worker.join(timeout=1)
        assert not worker.is_alive(), "diagnostics did not finish after Beam access was released"
        assert started.is_set()
        assert json.loads(result[0])["checks_total"] == 0
        started.clear()


def test_provider_diagnose_reports_isolated_bank_not_populated_default(
    tmp_path, monkeypatch, provider_modules
):
    """Provider diagnostics must report named-bank counts instead of default-bank counts."""
    from mnemosyne import diagnose
    from mnemosyne.core.memory import Mnemosyne

    monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(diagnose, "LOG_DIR", tmp_path / "logs")

    default_memory = Mnemosyne(session_id="default-session")
    default_memory.beam.remember("default one", source="test")
    default_memory.beam.remember("default two", source="test")
    isolated_memory = Mnemosyne(session_id="isolated-session", bank="isolated-profile")
    isolated_memory.beam.remember("isolated one", source="test")

    def active_bank_rows():
        """Snapshot the source rows a vec_working repair is allowed to inspect."""
        conn = isolated_memory.beam.conn
        snapshot: dict[str, object] = {}
        for table in ("working_memory", "memory_embeddings", "vec_working"):
            try:
                columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
                order_column = columns[0]
                snapshot[table] = conn.execute(
                    f"SELECT * FROM {table} ORDER BY {order_column}"
                ).fetchall()
            except Exception as exc:
                snapshot[table] = ("unavailable", type(exc).__name__)
        return snapshot

    def vec_working_output(result):
        return {
            key: result[key]
            for key in (
                "active_provider_vec_working",
                "active_provider_vec_working_error",
                "active_provider_vec_working_repair",
            )
            if key in result
        }

    observed = {}
    for name, module in provider_modules.items():
        provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
        provider._beam = isolated_memory.beam
        provider._profile_isolation_enabled = True
        provider._resolve_profile_bank = lambda: "isolated-profile"
        provider._sync_turn_diagnostics = lambda: {}

        result = json.loads(provider._handle_diagnose({}))
        entries = {entry["check"]: entry["status"] for entry in result["entries"]}
        source_before_dry_run = active_bank_rows()
        dry_run = json.loads(provider._handle_diagnose({"repair_vec_working": True, "dry_run": True}))
        source_after_dry_run = active_bank_rows()
        assert source_after_dry_run == source_before_dry_run
        observed[name] = {
            "resolved_bank": result["resolved_bank"],
            "working_total": entries["working_total"],
            "db_path": entries["db_path"],
            "vec_working": vec_working_output(result),
            "vec_working_dry_run": vec_working_output(dry_run),
        }

    assert {
        name: {key: observed[name][key] for key in ("resolved_bank", "working_total", "db_path")}
        for name in provider_modules
    } == {
        name: {
            "resolved_bank": "isolated-profile",
            "working_total": "1",
            "db_path": str(isolated_memory.db_path),
        }
        for name in provider_modules
    }
    for result in observed.values():
        assert set(result["vec_working"]) & {
            "active_provider_vec_working",
            "active_provider_vec_working_error",
        }
        assert set(result["vec_working_dry_run"]) & {
            "active_provider_vec_working_repair",
            "active_provider_vec_working_error",
        }
        repair = result["vec_working_dry_run"].get("active_provider_vec_working_repair")
        if repair is not None:
            assert repair["status"] == "dry_run"
            assert repair["inserted"] == 0
            assert repair["after"] == repair["before"]
    assert observed["hermes_memory_provider"]["vec_working"] == observed["mnemosyne_hermes"]["vec_working"]
    assert observed["hermes_memory_provider"]["vec_working_dry_run"] == observed["mnemosyne_hermes"][
        "vec_working_dry_run"
    ]


def test_packaged_provider_auto_sleep_uses_worker_local_beam(monkeypatch, provider_modules):
    """The packaged daemon must never pass its main-thread Beam into sleep."""
    module = provider_modules["mnemosyne_hermes"]
    provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
    source_calls = []
    worker_beams = []

    class SourceBeam:
        session_id = "session-a"
        db_path = "/tmp/isolated.db"
        author_id = "author-a"
        author_type = "user"
        channel_id = "channel-a"

        def get_working_stats(self):
            return {"total": 2}

        def _count_unconsolidated_before(self, _cutoff):
            return 1

        def sleep_all_sessions(self):
            source_calls.append("sleep_all_sessions")

        def sleep(self):
            source_calls.append("sleep")

    class WorkerBeam:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            worker_beams.append(self)

        def sleep_all_sessions(self):
            source_calls.append(("worker", "sleep_all_sessions"))

        def sleep(self):
            source_calls.append(("worker", "sleep"))

    class InlineThread:
        def __init__(self, *, target, daemon):
            assert daemon is True
            self._target = target

        def start(self):
            self._target()

        def join(self, timeout=None):
            assert timeout == provider._AUTO_SLEEP_TIMEOUT_SECONDS

        def is_alive(self):
            return False

    provider._beam = SourceBeam()
    provider._auto_sleep_threshold = 1
    provider._beam_access_lock = threading.Lock()
    provider._reserve_reflection_budget = lambda _reason: None
    monkeypatch.setattr(module, "_get_beam_class", lambda: WorkerBeam)
    monkeypatch.setattr(module, "threading", types.SimpleNamespace(Thread=InlineThread))

    provider._maybe_auto_sleep()

    assert len(worker_beams) == 1
    assert worker_beams[0] is not provider._beam
    assert worker_beams[0].kwargs == {
        "session_id": "session-a",
        "db_path": "/tmp/isolated.db",
        "author_id": "author-a",
        "author_type": "user",
        "channel_id": "channel-a",
    }
    assert source_calls == [("worker", "sleep_all_sessions")]


def test_provider_remember_extract_uses_default_scope(provider_modules):
    observed = {}
    for name, module in provider_modules.items():
        provider = _new_provider(module, scope="session")
        result = json.loads(provider._handle_remember({
            "content": f"extract scope {name}",
            "extract": True,
        }))
        observed[name] = {
            "status": result.get("status"),
            "scope": provider._beam.calls[0]["scope"],
        }

    assert observed["hermes_memory_provider"] == observed["mnemosyne_hermes"]
    assert observed["hermes_memory_provider"] == {"status": "stored", "scope": "session"}


@pytest.mark.parametrize("scope", ["session", "global"])
def test_provider_sync_turn_scope_and_truncation_match(monkeypatch, provider_modules, scope):
    monkeypatch.setenv("MNEMOSYNE_SYNC_TURN_USER_LIMIT", "7")
    monkeypatch.setenv("MNEMOSYNE_SYNC_TURN_ASSISTANT_LIMIT", "9")

    observed = {}
    for name, module in provider_modules.items():
        provider = _new_provider(module, scope=scope)
        provider.sync_turn("user-content", "assistant-content")
        observed[name] = provider._beam.calls

    assert observed["hermes_memory_provider"] == observed["mnemosyne_hermes"]
    assert [call["scope"] for call in observed["hermes_memory_provider"]] == [scope, scope]
    assert [call["content"] for call in observed["hermes_memory_provider"]] == [
        "[USER] user-co",
        "[ASSISTANT] assistant",
    ]


def test_provider_sync_turn_zero_limit_means_untruncated(monkeypatch, provider_modules):
    monkeypatch.setenv("MNEMOSYNE_SYNC_TURN_USER_LIMIT", "0")
    monkeypatch.setenv("MNEMOSYNE_SYNC_TURN_ASSISTANT_LIMIT", "0")

    observed = {}
    for name, module in provider_modules.items():
        provider = _new_provider(module)
        provider.sync_turn("user-content", "assistant-content")
        observed[name] = [call["content"] for call in provider._beam.calls]

    assert observed["hermes_memory_provider"] == observed["mnemosyne_hermes"]
    assert observed["hermes_memory_provider"] == [
        "[USER] user-content",
        "[ASSISTANT] assistant-content",
    ]


def _cache_provider(module):
    """Build a minimal live provider without invoking a real Beam recall."""
    provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
    provider._beam = object()
    provider._agent_context = ""
    provider._skip_contexts = set()
    provider._session_id = "provider-session"
    # The packaged provider retries initialization at the public hook boundary.
    provider._maybe_retry_init = lambda: None
    return provider


def _install_renderer(provider):
    calls = []
    cache_lock_violations = []

    def render(query, *, session_id=""):
        # Cache synchronization must not be held while the potentially slow
        # render/Beam path is running.
        if provider._prefetch_cache_lock.acquire(blocking=False):
            provider._prefetch_cache_lock.release()
        else:
            cache_lock_violations.append((query, session_id))
        calls.append((query, session_id))
        return f"rendered:{query}:{session_id}"

    provider._render_prefetch = render
    return calls, cache_lock_violations


@pytest.mark.parametrize("provider_name", ["hermes_memory_provider", "mnemosyne_hermes"])
def test_prefetch_cache_cold_miss_does_not_render_in_foreground(provider_modules, provider_name):
    """A cold pre-LLM lookup must not wait for the potentially slow recall path."""
    provider = _cache_provider(provider_modules[provider_name])
    calls = []
    renderer_started = threading.Event()
    renderer_release = threading.Event()

    def slow_render(query, *, session_id=""):
        calls.append((query, session_id))
        renderer_started.set()
        renderer_release.wait(timeout=1)
        return f"rendered:{query}:{session_id}"

    provider._render_prefetch = slow_render
    try:
        assert provider.prefetch("cold query", session_id="session-a") == ""
        assert calls == []
        assert not renderer_started.is_set()
    finally:
        renderer_release.set()

    diagnostics = provider._prefetch_cache_diagnostics()
    assert diagnostics["hits"] == 0
    assert diagnostics["misses"] == 1
    assert diagnostics["warm_completed"] == 0


def test_queue_prefetch_serializes_full_render_with_sync_turn_write(provider_modules):
    """A caller-owned warm render cannot overlap the provider's sync-turn DB phase."""
    for module in provider_modules.values():
        provider = _new_provider(module, roles=("user",))
        write_entered = threading.Event()
        release_write = threading.Event()
        render_started = threading.Event()

        class BlockingBeam:
            def remember(self, **_kwargs):
                write_entered.set()
                assert release_write.wait(timeout=1), "test did not release sync_turn"

        provider._beam = BlockingBeam()
        lock = _ObservedLock()
        provider._beam_access_lock = lock

        def controlled_render(_query, *, session_id=""):
            # Rendering must retain Beam serialization but never the cache lock.
            assert provider._prefetch_cache_lock.acquire(blocking=False)
            provider._prefetch_cache_lock.release()
            render_started.set()
            return f"rendered:{session_id}"

        provider._render_prefetch = controlled_render
        sync_worker = threading.Thread(
            target=lambda: provider.sync_turn("sufficient user content", "")
        )
        warm_worker = threading.Thread(
            target=lambda: provider.queue_prefetch("query", session_id="session-a")
        )
        sync_worker.start()
        assert write_entered.wait(timeout=1), "sync_turn did not reach its protected write"
        lock.waiting.clear()
        warm_worker.start()
        try:
            assert lock.waiting.wait(timeout=1), "warm render did not attempt the shared Beam lock"
            assert not render_started.is_set(), "warm render overlapped sync_turn's protected write"
        finally:
            release_write.set()
        sync_worker.join(timeout=1)
        warm_worker.join(timeout=1)
        assert not sync_worker.is_alive()
        assert not warm_worker.is_alive()
        assert render_started.is_set()
        # The committed sync turn may invalidate the just-warmed entry; this
        # regression proves serialization, not cache publication ordering.
        assert provider._prefetch_cache_diagnostics()["warm_completed"] == 1


def test_prefetch_cache_normalizes_exact_warm_hits_and_never_spawns_threads(
    monkeypatch, provider_modules
):
    """Warm results are reusable only after NFKC/case/whitespace normalization."""
    for module in provider_modules.values():
        provider = _cache_provider(module)
        provider._ensure_prefetch_cache()
        calls, cache_lock_violations = _install_renderer(provider)
        original_threading = module.threading
        created_threads = []

        class TrackingThreading:
            def Thread(self, *args, **kwargs):
                created_threads.append((args, kwargs))
                return original_threading.Thread(*args, **kwargs)

            def __getattr__(self, name):
                return getattr(original_threading, name)

        # Replace only this provider module's binding. The proxy delegates every
        # non-Thread attribute to stdlib threading, avoiding process-wide state.
        monkeypatch.setattr(module, "threading", TrackingThreading())
        provider.queue_prefetch("  ＣＡＦＥ\u3000PLAN  ", session_id="session-a")
        assert provider.prefetch("cafe plan", session_id="session-a") == "rendered:  ＣＡＦＥ\u3000PLAN  :session-a"
        assert calls == [("  ＣＡＦＥ\u3000PLAN  ", "session-a")]
        assert cache_lock_violations == []
        assert created_threads == []
        diagnostics = provider._prefetch_cache_diagnostics()
        assert diagnostics["hits"] == 1
        assert diagnostics["misses"] == 0
        assert diagnostics["warm_completed"] == 1


@pytest.mark.parametrize("provider_name", ["hermes_memory_provider", "mnemosyne_hermes"])
def test_prefetch_cache_uses_strict_session_and_normalized_query_match(
    monkeypatch, provider_modules, provider_name
):
    """A digest collision cannot leak a warm result across query or session boundaries."""
    collision_hash = types.SimpleNamespace(sha256=lambda _payload: types.SimpleNamespace(hexdigest=lambda: "same"))
    module = provider_modules[provider_name]
    provider = _cache_provider(module)
    calls, cache_lock_violations = _install_renderer(provider)
    monkeypatch.setattr(module, "hashlib", collision_hash)

    provider.queue_prefetch("alpha", session_id="session-a")
    assert provider.prefetch("beta", session_id="session-a") == ""
    assert calls == [("alpha", "session-a")]
    assert provider.prefetch("alpha", session_id="session-b") == ""
    assert calls == [("alpha", "session-a")]
    assert provider.prefetch("  ALPHA  ", session_id="session-a") == "rendered:alpha:session-a"
    assert calls == [("alpha", "session-a")]
    assert cache_lock_violations == []
    diagnostics = provider._prefetch_cache_diagnostics()
    assert diagnostics["hits"] == 1
    assert diagnostics["misses"] == 2


def test_prefetch_cache_lru_zero_config_and_schema_parity(tmp_path, provider_modules):
    """Cache capacity is bounded, recent hits refresh LRU order, and zero stores nothing."""
    observed = {}
    for name, module in provider_modules.items():
        provider = _cache_provider(module)
        provider._prefetch_cache_size = 2
        calls, cache_lock_violations = _install_renderer(provider)
        provider.queue_prefetch("one", session_id="s")
        provider.queue_prefetch("two", session_id="s")
        assert provider.prefetch("one", session_id="s") == "rendered:one:s"
        provider.queue_prefetch("three", session_id="s")
        assert provider.prefetch("one", session_id="s") == "rendered:one:s"
        assert provider.prefetch("two", session_id="s") == ""
        provider._prefetch_cache_size = 0
        provider._invalidate_prefetch_cache()
        provider.queue_prefetch("zero", session_id="s")
        assert provider.prefetch("zero", session_id="s") == ""
        assert cache_lock_violations == []
        observed[name] = {
            "calls": calls,
            "diagnostics": provider._prefetch_cache_diagnostics(),
        }

        hermes_home = tmp_path / name
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text(
            "memory:\n  provider: mnemosyne\n  mnemosyne:\n    prefetch_cache_size: 0\n"
        )
        configured = _provider_for_config(module, hermes_home)
        configured._apply_provider_config({})
        assert configured._prefetch_cache_size == 0
        configured._apply_provider_config({"prefetch_cache_size": 3})
        assert configured._prefetch_cache_size == 3

    assert observed["hermes_memory_provider"]["calls"] == observed["mnemosyne_hermes"]["calls"]
    assert observed["hermes_memory_provider"]["calls"] == [
        ("one", "s"), ("two", "s"), ("three", "s"), ("zero", "s"),
    ]
    for result in observed.values():
        assert result["diagnostics"]["entries"] == 0
        assert result["diagnostics"]["evictions"] == 1
        assert result["diagnostics"]["warm_completed"] == 4
        assert result["diagnostics"]["misses"] == 2


def test_prefetch_cache_rejects_late_warm_after_epoch_invalidation(provider_modules):
    """A reinitialize/invalidation race cannot publish context from the old epoch."""
    for module in provider_modules.values():
        provider = _cache_provider(module)
        provider._ensure_prefetch_cache()

        def late_render(_query, *, session_id=""):
            provider._invalidate_prefetch_cache()
            return f"late:{session_id}"

        provider._render_prefetch = late_render
        provider.queue_prefetch("old query", session_id="old-session")
        assert provider._prefetch_cache == {}
        assert provider._prefetch_cache_diagnostics()["warm_completed"] == 1


def test_prefetch_cache_invalidates_only_after_successful_writes_including_memory_hook(
    monkeypatch, provider_modules
):
    """Failed/staged writes keep a warm result; completed mutations invalidate it."""
    for module in provider_modules.values():
        provider = _cache_provider(module)
        invalidations = []
        original_invalidate = provider._invalidate_prefetch_cache

        def record_invalidate():
            invalidations.append("invalidate")
            original_invalidate()

        provider._invalidate_prefetch_cache = record_invalidate
        provider._default_scope = "session"
        provider._audit_event = lambda *_args, **_kwargs: None

        monkeypatch.setattr(module, "_write_approval_enabled", lambda: False)
        monkeypatch.setattr(module, "apply_beam_batch", lambda *_args, **_kwargs: {"status": "failed"})
        assert json.loads(provider._handle_batch({"operations": [{"action": "remember", "content": "x"}]}))["status"] == "failed"
        assert invalidations == []
        monkeypatch.setattr(module, "apply_beam_batch", lambda *_args, **_kwargs: {"status": "ok"})
        assert json.loads(provider._handle_batch({"operations": [{"action": "remember", "content": "x"}]}))["status"] == "ok"
        assert invalidations == ["invalidate"]

        staged_writes = []
        monkeypatch.setattr(module, "_write_approval_enabled", lambda: True)
        monkeypatch.setattr(
            module,
            "_stage_pending_write",
            lambda payload: staged_writes.append(payload) or f"pending-{len(staged_writes)}",
        )
        monkeypatch.setattr(
            module,
            "apply_beam_batch",
            lambda *_args, **_kwargs: pytest.fail("approval-gated batch must not write"),
        )
        staged = json.loads(provider._handle_batch({"operations": [{"action": "remember", "content": "x"}]}))
        assert staged["status"] == "staged"
        staged_ids = staged.get("pending_ids") or [item["pending_id"] for item in staged["staged"]]
        assert staged_ids == ["pending-1"]
        assert staged.get("count", staged.get("staged_count")) == 1
        assert len(staged_writes) == 1
        assert staged_writes[0]["tool"] == "mnemosyne_batch"
        assert staged_writes[0]["action"] == "remember"
        assert staged_writes[0]["scope"] == "session"
        assert invalidations == ["invalidate"]

        class FailingBeam:
            def remember(self, **_kwargs):
                raise RuntimeError("write failed")

        provider._beam = FailingBeam()
        provider.on_memory_write("add", "user", "private write")
        provider.on_memory_write("delete", "user", "private write")
        assert invalidations == ["invalidate"]

        class SuccessBeam:
            def remember(self, **_kwargs):
                return "memory-id"

        provider._beam = SuccessBeam()
        provider.on_memory_write("replace", "agent", "private write")
        assert invalidations == ["invalidate", "invalidate"]

        class TaskStore:
            retired = False

            def forget(self, *_args):
                return self.retired

        store = TaskStore()
        provider._beam = types.SimpleNamespace(canonical=store)
        provider._canonical_owner = lambda: "owner"
        assert json.loads(provider._handle_task_progress({"action": "clear", "task": "job"}))["status"] == "cleared"
        assert invalidations == ["invalidate", "invalidate"]
        store.retired = True
        assert json.loads(provider._handle_task_progress({"action": "clear", "task": "job"}))["status"] == "cleared"
        assert invalidations == ["invalidate", "invalidate", "invalidate"]


def test_sync_turn_invalidates_after_committed_write_even_if_identity_capture_fails(provider_modules):
    """A post-commit identity-capture failure cannot leave a warm result stale."""
    for module in provider_modules.values():
        provider = _new_provider(module, roles=("user",))
        invalidations = []
        provider._invalidate_prefetch_cache = lambda: invalidations.append("invalidate")

        class PartialBeam:
            def __init__(self):
                self.calls = 0

            def remember(self, **_kwargs):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("identity capture failed after user commit")

        provider._beam = PartialBeam()
        del provider._capture_identity_signals
        provider.sync_turn("I feel like a capable engineer", "")
        assert provider._beam.calls == 2
        assert invalidations == ["invalidate"]
        assert provider._sync_turn_diagnostics()["failed"] == 1

        class FailingBeam:
            def remember(self, **_kwargs):
                raise RuntimeError("user write failed before commit")

        provider = _new_provider(module, roles=("user",))
        provider._beam = FailingBeam()
        provider._invalidate_prefetch_cache = lambda: invalidations.append("unexpected")
        provider.sync_turn("ordinary user content", "")
        assert invalidations == ["invalidate"]

        provider = _new_provider(module, roles=())
        provider._invalidate_prefetch_cache = lambda: invalidations.append("unexpected")
        provider.sync_turn("ordinary user content", "")
        assert invalidations == ["invalidate"]


@pytest.mark.parametrize(
    ("sleep_status", "expected_invalidations"),
    [("no_op", 0), ("consolidated", 1)],
)
def test_prefetch_cache_sleep_workers_invalidate_only_after_consolidation(
    monkeypatch, provider_modules, sleep_status, expected_invalidations
):
    """Auto- and session-end sleep preserve warm cache when no consolidation occurs."""
    for module in provider_modules.values():
        class SourceBeam:
            session_id = "session"
            db_path = "test.db"
            author_id = "author"
            author_type = "agent"
            channel_id = "channel"

            def get_working_stats(self):
                return {"total": 2}

            def _count_unconsolidated_before(self, _cutoff):
                return 1

            def sleep(self):
                return {"status": sleep_status}

        class IsolatedBeam:
            def __init__(self, **_kwargs):
                pass

            def sleep(self):
                return {"status": sleep_status}

        monkeypatch.setattr(module, "_get_beam_class", lambda: IsolatedBeam)
        for worker in ("auto", "session_end"):
            provider = _cache_provider(module)
            provider._beam = SourceBeam()
            provider._beam_access_lock = threading.Lock()
            provider._auto_sleep_threshold = 1
            provider._AUTO_SLEEP_TIMEOUT_SECONDS = 1
            provider.SESSION_END_SLEEP_TIMEOUT_SECONDS = 1
            provider._reserve_reflection_budget = lambda _source: None
            provider._ensure_prefetch_cache()
            provider._prefetch_cache["warm"] = "cached result"
            invalidations = []
            original_invalidate = provider._invalidate_prefetch_cache

            def record_invalidate():
                invalidations.append("invalidate")
                original_invalidate()

            provider._invalidate_prefetch_cache = record_invalidate
            if worker == "auto":
                provider._maybe_auto_sleep()
            else:
                provider.on_session_end(messages=[])
                provider._session_end_thread.join(timeout=1)

            assert invalidations == ["invalidate"] * expected_invalidations
            assert (provider._prefetch_cache == {}) is (expected_invalidations == 1)


@pytest.mark.parametrize(
    ("path", "result", "expected_invalidations"),
    [
        ("provider", {"imported": 0, "skipped": 1, "failed": 1}, 0),
        ("provider", {"imported": 1, "skipped": 0, "failed": 0}, 1),
        (
            "file",
            {
                "beam": {"working_memory": {"inserted": 0, "skipped": 2, "overwritten": 0}},
                "triples": {"inserted": 0, "skipped": 1, "overwritten": 0},
            },
            0,
        ),
        (
            "file",
            {
                "beam": {"scratchpad": {"inserted": 0, "updated": 1}},
                "triples": {"inserted": 0, "skipped": 0, "overwritten": 1},
            },
            1,
        ),
    ],
)
def test_prefetch_cache_import_invalidates_only_after_successful_writes(
    monkeypatch, provider_modules, path, result, expected_invalidations
):
    """Provider and file imports leave warm cache intact for zero-write outcomes."""
    from mnemosyne.core import importers
    from mnemosyne.core import memory as memory_module
    from mnemosyne.core.importers.base import ImporterResult

    class FakeMemory:
        def __init__(self, **_kwargs):
            pass

        def import_from_file(self, _input_path, *, force):
            assert force is False
            return result

    monkeypatch.setattr(memory_module, "Mnemosyne", FakeMemory)
    for module in provider_modules.values():
        provider = _cache_provider(module)
        provider._beam = types.SimpleNamespace(db_path="test.db")
        provider._session_id = "session"
        provider._audit_event = lambda *_args, **_kwargs: None
        invalidations = []
        provider._invalidate_prefetch_cache = lambda: invalidations.append("invalidate")

        if path == "provider":
            importer_result = ImporterResult("fake", **result)
            monkeypatch.setattr(importers, "import_from_provider", lambda *_args, **_kwargs: importer_result)
            payload = json.loads(provider._handle_import({"provider": "fake", "api_key": "key"}))
            assert payload["imported"] == result["imported"]
        else:
            payload = json.loads(provider._handle_import({"input_path": "export.json"}))
            assert payload["stats"] == result

        assert invalidations == ["invalidate"] * expected_invalidations


@pytest.mark.parametrize("provider_name", ["hermes_memory_provider", "mnemosyne_hermes"])
def test_prefetch_cache_invalidates_after_successful_tool_mutations(
    monkeypatch, provider_modules, provider_name
):
    """Every successful mutation invalidates after its write in both provider copies."""
    module = provider_modules[provider_name]
    provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
    invalidations = []

    class Graph:
        def add_edge(self, edge):
            self.edge = edge

    class Beam:
        db_path = "test.db"
        episodic_graph = Graph()

        def scratchpad_write(self, content):
            self.written = content
            return "pad-1"

        def scratchpad_clear(self):
            self.cleared = True

    provider._beam = Beam()
    provider._invalidate_prefetch_cache = lambda: invalidations.append("invalidate")
    monkeypatch.setattr(module, "_get_triple_module", lambda: (lambda *_args, **_kwargs: "triple-1", None))
    monkeypatch.setattr("mnemosyne.core.triples.end_triple", lambda *_args, **_kwargs: 1)

    assert json.loads(provider._handle_triple_add({"subject": "a", "predicate": "is", "object": "b"})) == {
        "status": "stored", "triple_id": "triple-1"
    }
    assert json.loads(provider._handle_triple_end({"subject": "a", "predicate": "is"})) == {
        "status": "ended", "count": 1
    }
    assert json.loads(provider._handle_graph_link({
        "source_id": "memory-a", "target_id": "memory-b", "relationship": "related",
    }))["status"] == "linked"
    assert json.loads(provider._handle_scratchpad_write({"content": "working note"})) == {
        "status": "written", "id": "pad-1"
    }
    assert json.loads(provider._handle_scratchpad_clear({})) == {"status": "cleared"}
    assert invalidations == ["invalidate"] * 5


@pytest.mark.parametrize("provider_name", ["hermes_memory_provider", "mnemosyne_hermes"])
def test_prefetch_cache_skips_invalidations_for_tool_failures_and_noops(
    monkeypatch, provider_modules, provider_name
):
    """Validation errors, failed writes, and a zero-count end leave warm results intact."""
    module = provider_modules[provider_name]
    provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
    invalidations = []

    class FailingGraph:
        def add_edge(self, _edge):
            raise RuntimeError("graph write failed")

    class FailingBeam:
        db_path = "test.db"
        episodic_graph = FailingGraph()

        def scratchpad_write(self, _content):
            raise RuntimeError("scratchpad write failed")

        def scratchpad_clear(self):
            raise RuntimeError("scratchpad clear failed")

    provider._beam = FailingBeam()
    provider._invalidate_prefetch_cache = lambda: invalidations.append("invalidate")
    monkeypatch.setattr(module, "_get_triple_module", lambda: (lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("triple add failed")), None))
    monkeypatch.setattr("mnemosyne.core.triples.end_triple", lambda *_args, **_kwargs: 0)

    assert "error" in json.loads(provider._handle_triple_add({}))
    assert json.loads(provider._handle_triple_end({"subject": "a", "predicate": "is"})) == {
        "status": "ended", "count": 0
    }
    assert "error" in json.loads(provider._handle_graph_link({"source_id": "", "target_id": "b", "relationship": "related"}))
    assert "error" in json.loads(provider._handle_scratchpad_write({"content": "  "}))
    with pytest.raises(RuntimeError, match="triple add failed"):
        provider._handle_triple_add({"subject": "a", "predicate": "is", "object": "b"})
    with pytest.raises(RuntimeError, match="graph write failed"):
        provider._handle_graph_link({"source_id": "a", "target_id": "b", "relationship": "related"})
    with pytest.raises(RuntimeError, match="scratchpad write failed"):
        provider._handle_scratchpad_write({"content": "working note"})
    with pytest.raises(RuntimeError, match="scratchpad clear failed"):
        provider._handle_scratchpad_clear({})
    assert invalidations == []


@pytest.mark.parametrize("active", [False, True])
def test_prefetch_cache_diagnostics_are_present_on_both_paths_and_private(
    monkeypatch, provider_modules, active
):
    """Diagnostics publish counters only—never warm query/session/content values."""
    secret_query = "QUERY-SECRET-998"
    secret_session = "SESSION-SECRET-998"

    def fake_run_diagnostics(**_kwargs):
        return {"checks_total": 0, "key_findings": [], "entries": []}

    monkeypatch.setattr("mnemosyne.diagnose.run_diagnostics", fake_run_diagnostics)
    for module in provider_modules.values():
        provider = _cache_provider(module)
        provider._profile_isolation_enabled = False
        provider._sync_turn_diagnostics = lambda: {}
        _calls, cache_lock_violations = _install_renderer(provider)
        provider.queue_prefetch(secret_query, session_id=secret_session)
        provider._beam = object() if active else None
        payload = json.loads(provider._handle_diagnose({}))
        cache = payload["prefetch_cache"]
        assert cache["capacity"] == 50
        assert cache["warm_completed"] == 1
        assert cache["entries"] == 1
        assert cache_lock_violations == []
        assert secret_query not in json.dumps(payload)
        assert secret_session not in json.dumps(payload)


def test_sync_adapter_schema_and_lifecycle_surface_match(sync_modules):
    root_sync = sync_modules["hermes_memory_provider"]
    integration_sync = sync_modules["mnemosyne_hermes"]

    assert _json_stable(integration_sync.ALL_SYNC_TOOL_SCHEMAS) == _json_stable(root_sync.ALL_SYNC_TOOL_SCHEMAS)

    for module in sync_modules.values():
        adapter = module.SyncAdapter.__new__(module.SyncAdapter)
        adapter._engine = object()
        assert adapter.start() is True
        assert _json_stable(adapter.tool_schemas) == _json_stable(root_sync.ALL_SYNC_TOOL_SCHEMAS)
        adapter.shutdown()
        assert adapter.tool_schemas == []


class _FakeSyncEngine:
    def __init__(self, beam_instance, encryption=None):
        self.beam_instance = beam_instance
        self.encryption = encryption
        self.device_id = "fake-device"


class _FakeSyncEncryption:
    def __init__(self, key_source):
        self.key_source = key_source

    @classmethod
    def from_config(cls, key_source=None, **_kwargs):
        return cls(key_source)


class _UnexpectedBeam:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def _install_fake_sync_modules(monkeypatch):
    import types

    fake_sync = types.ModuleType("mnemosyne.core.sync")
    fake_sync.SyncEngine = _FakeSyncEngine
    fake_sync.SyncEncryption = _FakeSyncEncryption
    fake_beam = types.ModuleType("mnemosyne.core.beam")
    fake_beam.BeamMemory = _UnexpectedBeam
    monkeypatch.setitem(sys.modules, "mnemosyne.core.sync", fake_sync)
    monkeypatch.setitem(sys.modules, "mnemosyne.core.beam", fake_beam)


def test_sync_adapter_uses_provider_beam_for_both_surfaces(monkeypatch, sync_modules):
    _install_fake_sync_modules(monkeypatch)

    provider_beam = object()
    for module in sync_modules.values():
        adapter = module.SyncAdapter(provider_beam, {})
        assert adapter.is_ready is True
        assert adapter._engine.beam_instance is provider_beam


def test_sync_adapter_config_resolution_matches(monkeypatch, sync_modules):
    _install_fake_sync_modules(monkeypatch)
    monkeypatch.delenv("MNEMOSYNE_SYNC_REMOTE", raising=False)
    monkeypatch.setenv("MNEMOSYNE_SYNC_HOST", "sync.example")
    monkeypatch.setenv("MNEMOSYNE_SYNC_PORT", "443")

    observed = {}
    for name, module in sync_modules.items():
        adapter = module.SyncAdapter(object(), {"encrypt": True, "key": "encoded-key"})
        observed[name] = {
            "remote": adapter.remote,
            "encryption_key_source": adapter._engine.encryption.key_source,
        }

    assert observed["mnemosyne_hermes"] == observed["hermes_memory_provider"]
    assert observed["hermes_memory_provider"] == {
        "remote": "https://sync.example:443",
        "encryption_key_source": "encoded-key",
    }


def test_sync_adapter_key_source_file_preserves_path_case(tmp_path, sync_modules):
    key_file = tmp_path / "MixedCaseSync.key"
    key_file.write_text("file-key")

    observed = {}
    for name, module in sync_modules.items():
        adapter = module.SyncAdapter.__new__(module.SyncAdapter)
        adapter._config = {"key_source": f"FILE:{key_file}"}
        observed[name] = adapter._resolve_key()

    assert observed["mnemosyne_hermes"] == observed["hermes_memory_provider"]
    assert observed["hermes_memory_provider"] == "file-key"


class _ToolEngine:
    device_id = "device-1"

    def __init__(self, *, local_next_cursor: str | None = "local-cursor"):
        self.meta = {"last_sync_cursor": "cursor-previous"}
        self.conn = self
        self.local_next_cursor = local_next_cursor

    def _meta_get(self, key):
        return self.meta.get(key)

    def _meta_set(self, key, value):
        self.meta[key] = value

    def pull_changes(self, since_cursor=None, limit=500):
        return {"events": [{"id": "e1"}], "next_cursor": self.local_next_cursor}

    def push_changes(self, events):
        self.pushed_events = events
        return {"accepted": 2, "duplicates": 1, "conflicts": 1}

    def execute(self, _sql):
        return self

    def fetchone(self):
        return (3,)


def _adapter_with_tool_engine(
    module,
    *,
    next_cursor: str | None = "remote-cursor",
    local_next_cursor: str | None = "local-cursor",
):
    adapter = module.SyncAdapter.__new__(module.SyncAdapter)
    adapter._engine = _ToolEngine(local_next_cursor=local_next_cursor)
    adapter._error = None
    adapter.remote = "https://sync.example"
    adapter.encrypt_enabled = False
    adapter.mode = "bidirectional"
    adapter.auth_token = ""

    def fake_post(_path, _payload):
        return {
            "status": "ok",
            "accepted": 2,
            "duplicates": 1,
            "conflicts": 1,
            "events": [{"id": "remote-1"}, {"id": "remote-2"}],
            "next_cursor": next_cursor,
        }

    adapter._http_post = fake_post
    adapter._post = fake_post
    return adapter


def test_sync_adapter_tool_results_match(sync_modules):
    observed = {}
    for name, module in sync_modules.items():
        adapter = _adapter_with_tool_engine(module)
        observed[name] = {
            "push": json.loads(adapter.handle_tool_call("mnemosyne_sync_push", {})),
            "pull": json.loads(adapter.handle_tool_call("mnemosyne_sync_pull", {})),
            "status": json.loads(adapter.handle_tool_call("mnemosyne_sync_status", {})),
            "unknown": json.loads(adapter.handle_tool_call("mnemosyne_sync_unknown", {})),
        }

    assert observed["mnemosyne_hermes"] == observed["hermes_memory_provider"]
    assert observed["hermes_memory_provider"]["push"] == {
        "status": "ok",
        "pushed": 2,
        "duplicates": 1,
        "conflicts": 1,
        "next_cursor": "remote-cursor",
    }
    assert observed["hermes_memory_provider"]["pull"] == {
        "status": "ok",
        "pulled": 2,
        "duplicates": 1,
        "conflicts": 1,
        "next_cursor": "remote-cursor",
    }


def test_sync_adapter_push_tolerates_null_next_cursor(sync_modules):
    observed = {}
    for name, module in sync_modules.items():
        adapter = _adapter_with_tool_engine(module, next_cursor=None, local_next_cursor=None)
        observed[name] = json.loads(adapter.handle_tool_call("mnemosyne_sync_push", {}))

    assert observed["mnemosyne_hermes"] == observed["hermes_memory_provider"]
    assert observed["hermes_memory_provider"] == {
        "status": "ok",
        "pushed": 2,
        "duplicates": 1,
        "conflicts": 1,
        "next_cursor": "",
    }



def test_sync_adapter_pull_tolerates_null_next_cursor(sync_modules):
    observed = {}
    for name, module in sync_modules.items():
        adapter = _adapter_with_tool_engine(module, next_cursor=None)
        observed[name] = json.loads(adapter.handle_tool_call("mnemosyne_sync_pull", {}))

    assert observed["mnemosyne_hermes"] == observed["hermes_memory_provider"]
    assert observed["hermes_memory_provider"] == {
        "status": "ok",
        "pulled": 2,
        "duplicates": 1,
        "conflicts": 1,
        "next_cursor": "",
    }

def _prompt_provider(module):
    provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
    provider._beam = object()
    provider._init_error = None
    if hasattr(provider, "_persona_cache"):
        provider._persona_cache = {"mtime": None, "content": None}
    return provider


def test_provider_persona_prompt_injection_matches(tmp_path, provider_modules):
    persona_file = tmp_path / "persona.md"
    persona_file.write_text(
        "# Persona\n\n"
        "## privacy\n"
        "- expected persona/privacy rule [importance: 0.90]\n"
    )

    observed = {}
    for name, module in provider_modules.items():
        provider = _prompt_provider(module)
        # Class-level env defaults are read at import time; set the attrs
        # directly so both already-imported provider surfaces see this file.
        provider.PERSONA_ENABLED = True
        provider.PERSONA_FILE = persona_file
        observed[name] = provider.system_prompt_block()

    for block in observed.values():
        assert "# Mnemosyne Memory" in block
        assert "# L3 Persona (Active Behavioral Rules)" in block
        assert "expected persona/privacy rule" in block


def test_provider_persona_prompt_silent_when_disabled_or_missing(tmp_path, provider_modules):
    persona_file = tmp_path / "persona.md"
    persona_file.write_text("# Persona\n\n- should stay hidden when disabled\n")
    missing_file = tmp_path / "missing-persona.md"

    for module in provider_modules.values():
        provider = _prompt_provider(module)
        provider.PERSONA_ENABLED = False
        provider.PERSONA_FILE = persona_file
        block = provider.system_prompt_block()
        assert "# L3 Persona" not in block
        assert "should stay hidden when disabled" not in block

        provider = _prompt_provider(module)
        provider.PERSONA_ENABLED = True
        provider.PERSONA_FILE = missing_file
        assert "# L3 Persona" not in provider.system_prompt_block()


def test_provider_persona_negative_token_cap_does_not_slice_from_end(tmp_path, provider_modules):
    persona_file = tmp_path / "persona.md"
    persona_file.write_text("# Persona\n\n## privacy\n- secret tail should not leak\n")

    for module in provider_modules.values():
        provider = _prompt_provider(module)
        provider.PERSONA_ENABLED = True
        provider.PERSONA_FILE = persona_file
        provider.PERSONA_TOKEN_CAP = -10
        block = provider.system_prompt_block()
        assert "secret tail should not leak" not in block
        assert "truncated" in block


@pytest.mark.parametrize("bad_token_cap", ["", "not-an-int"])
def test_provider_persona_token_cap_invalid_env_falls_back(monkeypatch, bad_token_cap):
    monkeypatch.setenv("MNEMOSYNE_PERSONA_TOKEN_CAP", bad_token_cap)

    modules = {
        "hermes_memory_provider": _import_module("hermes_memory_provider", PROJECT_ROOT),
        "mnemosyne_hermes": _import_module("mnemosyne_hermes", INTEGRATION_SRC),
    }

    assert {name: module.MnemosyneMemoryProvider.PERSONA_TOKEN_CAP for name, module in modules.items()} == {
        "hermes_memory_provider": 1500,
        "mnemosyne_hermes": 1500,
    }


def test_packaged_provider_import_survives_missing_core_helpers():
    """Installer/status diagnostics must import even with a broken core install."""

    import importlib.abc

    blocked = {
        "mnemosyne.batch_tool",
        "mnemosyne.hermes_config",
        "mnemosyne.integrations.hermes_persona_prompt",
    }

    class _BlockCoreHelperImports(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname in blocked:
                raise ModuleNotFoundError(f"blocked test import: {fullname}")
            return None

    finder = _BlockCoreHelperImports()
    saved = {name: module for name, module in sys.modules.items() if name in blocked}
    for name in blocked:
        sys.modules.pop(name, None)
    _drop_modules("mnemosyne_hermes")
    sys.path.insert(0, str(INTEGRATION_SRC))
    sys.meta_path.insert(0, finder)
    try:
        module = importlib.import_module("mnemosyne_hermes")
    finally:
        sys.meta_path.remove(finder)
        try:
            sys.path.remove(str(INTEGRATION_SRC))
        except ValueError:
            pass
        for name in blocked:
            sys.modules.pop(name, None)
        sys.modules.update(saved)

    try:
        assert module.read_hermes_config_key(None, "tools") is None
        with pytest.raises(module.BatchValidationError):
            module.validate_batch_operations([])
        provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
        assert provider._with_persona_block("base") == "base"
    finally:
        _drop_modules("mnemosyne_hermes")
        _import_module("mnemosyne_hermes", INTEGRATION_SRC)


def _save_mnemosyne_modules():
    return {
        name: module for name, module in sys.modules.items()
        if name == "mnemosyne" or name.startswith("mnemosyne.")
    }


def _restore_mnemosyne_modules(saved_modules):
    for name in list(sys.modules):
        if name == "mnemosyne" or name.startswith("mnemosyne."):
            sys.modules.pop(name, None)
    sys.modules.update(saved_modules)


def test_provider_persona_tool_dispatch_matches(tmp_path, provider_modules):
    saved_mnemosyne_modules = _save_mnemosyne_modules()
    _drop_modules("mnemosyne")
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from mnemosyne.core.beam import BeamMemory

        observed = {}
        for name, module in provider_modules.items():
            db_path = tmp_path / f"{name}.db"
            beam = BeamMemory(session_id=f"persona-{name}", db_path=str(db_path))
            beam.conn.execute(
                "INSERT INTO memoria_persona (tier, topic, content, confidence) "
                "VALUES (?, ?, ?, ?)",
                ("long_term", "test", f"persona rule for {name}", 0.9),
            )
            beam.conn.commit()

            provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
            provider._beam = beam
            result = json.loads(provider.handle_tool_call("mnemosyne_persona_list", {}))
            observed[name] = {
                "status": result.get("status"),
                "count": result.get("count"),
                "topics": [row.get("topic") for row in result.get("personas", [])],
            }
    finally:
        try:
            sys.path.remove(str(PROJECT_ROOT))
        except ValueError:
            pass
        _restore_mnemosyne_modules(saved_mnemosyne_modules)

    assert observed["hermes_memory_provider"] == observed["mnemosyne_hermes"]
    assert observed["hermes_memory_provider"] == {
        "status": "ok",
        "count": 1,
        "topics": ["test"],
    }


def test_provider_batch_dispatch_matches(tmp_path, provider_modules):
    saved_mnemosyne_modules = _save_mnemosyne_modules()
    _drop_modules("mnemosyne")
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from mnemosyne.core.beam import BeamMemory

        observed = {}
        for name, module in provider_modules.items():
            db_path = tmp_path / f"{name}-batch.db"
            beam = BeamMemory(session_id=f"batch-{name}", db_path=str(db_path))
            provider = module.MnemosyneMemoryProvider.__new__(module.MnemosyneMemoryProvider)
            provider._beam = beam
            provider._hermes_home = str(tmp_path)
            provider._default_scope = "session"
            provider._audit_event = lambda *args, **kwargs: None

            result = json.loads(provider.handle_tool_call("mnemosyne_batch", {
                "operations": [
                    {"action": "remember", "content": f"batch parity {name}"},
                ],
            }))
            observed[name] = {
                "status": result.get("status"),
                "operations_count": result.get("operations_count"),
                "result_statuses": [row.get("status") for row in result.get("results", [])],
            }
    finally:
        try:
            sys.path.remove(str(PROJECT_ROOT))
        except ValueError:
            pass
        _restore_mnemosyne_modules(saved_mnemosyne_modules)

    assert observed["hermes_memory_provider"] == observed["mnemosyne_hermes"]
    assert observed["hermes_memory_provider"] == {
        "status": "ok",
        "operations_count": 1,
        "result_statuses": ["stored"],
    }
