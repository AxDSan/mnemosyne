import json
import sqlite3
from types import SimpleNamespace

from hermes_memory_provider.cli import mnemosyne_command
from mnemosyne.core.importers.base import ImporterResult
from mnemosyne.core.memory import Mnemosyne as RealMnemosyne


def _sample_items():
    return [
        {
            "id": "hs-world-1",
            "text": "Hermes CLI Hindsight import must preserve timestamps.",
            "fact_type": "world",
            "mentioned_at": "2026-04-29T01:36:00+00:00",
            "date": "2026-04-29",
            "proof_count": 2,
            "tags": ["session:cli-import"],
        }
    ]


def _args(**overrides):
    defaults = {
        "mnemosyne_cmd": "import",
        "list_providers": False,
        "generate_script": False,
        "agentic": False,
        "from_provider": None,
        "output_script": None,
        "input": None,
        "file": None,
        "force": False,
        "api_key": None,
        "user_id": None,
        "agent_id": None,
        "base_url": None,
        "bank": None,
        "dry_run": False,
        "session_id": None,
        "channel_id": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _patch_cli_memory(monkeypatch, db_path):
    class DummyBeamMemory:
        def __init__(self, *args, **kwargs):
            pass

    def make_memory(session_id="default", channel_id=None, **kwargs):
        return RealMnemosyne(session_id=session_id, channel_id=channel_id, db_path=db_path)

    # mnemosyne_command imports these symbols inside the function body, so
    # patching their definition modules before invocation keeps tests isolated.
    monkeypatch.setattr("mnemosyne.core.beam.BeamMemory", DummyBeamMemory)
    monkeypatch.setattr("mnemosyne.core.memory.Mnemosyne", make_memory)


def _write_export(tmp_path, items=None):
    export = tmp_path / "hindsight-export.json"
    export.write_text(json.dumps({"items": items or _sample_items()}), encoding="utf-8")
    return export


def _db_counts_and_row(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT timestamp, metadata_json FROM episodic_memory ORDER BY timestamp LIMIT 1"
            ).fetchone()
            working_count = conn.execute("SELECT COUNT(*) FROM working_memory").fetchone()[0]
            episodic_count = conn.execute("SELECT COUNT(*) FROM episodic_memory").fetchone()[0]
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            return 0, 0, None
    return working_count, episodic_count, row


def test_hermes_cli_hindsight_file_import_preserves_timestamp(tmp_path, monkeypatch):
    db_path = tmp_path / "mnemosyne.db"
    _patch_cli_memory(monkeypatch, db_path)
    export = _write_export(tmp_path)

    rc = mnemosyne_command(
        _args(from_provider="hindsight", file=str(export), bank="hermes")
    )

    assert rc == 0
    working_count, _, row = _db_counts_and_row(db_path)
    assert row["timestamp"] == "2026-04-29T01:36:00+00:00"
    assert working_count == 0


def test_hermes_cli_hindsight_accepts_input_alias(tmp_path, monkeypatch):
    db_path = tmp_path / "mnemosyne.db"
    _patch_cli_memory(monkeypatch, db_path)
    export = _write_export(tmp_path)

    rc = mnemosyne_command(
        _args(from_provider="hindsight", input=str(export), bank="hermes")
    )

    assert rc == 0
    working_count, _, row = _db_counts_and_row(db_path)
    assert row["timestamp"] == "2026-04-29T01:36:00+00:00"
    assert working_count == 0


def test_hermes_cli_hindsight_base_url_does_not_require_api_key(tmp_path, monkeypatch):
    db_path = tmp_path / "mnemosyne.db"
    _patch_cli_memory(monkeypatch, db_path)
    captured = {}

    def fake_import_from_provider(provider, mem, **kwargs):
        captured["provider"] = provider
        captured["kwargs"] = kwargs
        return ImporterResult(provider="hindsight", total=1, imported=1)

    monkeypatch.setattr(
        "mnemosyne.core.importers.import_from_provider", fake_import_from_provider
    )

    rc = mnemosyne_command(
        _args(
            from_provider="hindsight",
            base_url="http://127.0.0.1:8888",
            bank="personal",
        )
    )

    assert rc == 0
    assert captured["provider"] == "hindsight"
    assert captured["kwargs"]["base_url"] == "http://127.0.0.1:8888"
    assert captured["kwargs"]["file_path"] is None
    assert captured["kwargs"]["bank"] == "personal"
    assert "api_key" not in captured["kwargs"]


def test_hermes_cli_hindsight_forwards_non_default_bank(tmp_path, monkeypatch):
    db_path = tmp_path / "mnemosyne.db"
    _patch_cli_memory(monkeypatch, db_path)
    export = _write_export(tmp_path)

    rc = mnemosyne_command(
        _args(from_provider="hindsight", file=str(export), bank="personal")
    )

    assert rc == 0
    _, _, row = _db_counts_and_row(db_path)
    metadata = json.loads(row["metadata_json"])
    assert "hindsight_bank" in metadata
    assert metadata["hindsight_bank"] == "personal"


def test_hermes_cli_hindsight_dry_run_writes_no_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "mnemosyne.db"
    _patch_cli_memory(monkeypatch, db_path)
    export = _write_export(tmp_path)

    rc = mnemosyne_command(
        _args(from_provider="hindsight", file=str(export), dry_run=True)
    )

    assert rc == 0
    working_count, episodic_count, row = _db_counts_and_row(db_path)
    assert working_count == 0
    assert episodic_count == 0
    assert row is None


def test_hermes_cli_hindsight_requires_file_or_base_url(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "mnemosyne.db"
    _patch_cli_memory(monkeypatch, db_path)

    rc = mnemosyne_command(_args(from_provider="hindsight"))

    assert rc == 1
    output = capsys.readouterr().out
    assert "Hindsight import requires --file/--input or --base-url" in output


def test_hermes_cli_other_provider_still_requires_api_key(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "mnemosyne.db"
    _patch_cli_memory(monkeypatch, db_path)
    monkeypatch.delenv("MEM0_API_KEY", raising=False)

    rc = mnemosyne_command(_args(from_provider="mem0"))

    assert rc == 1
    output = capsys.readouterr().out
    assert "--api-key required for mem0 import" in output
