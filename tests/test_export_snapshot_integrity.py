"""Regression coverage for portable-export snapshot integrity."""

import json
import sqlite3
import threading

from mnemosyne.core.memory import Mnemosyne, _export_completeness


def test_export_payload_and_manifest_share_one_read_snapshot(tmp_path, monkeypatch):
    db_path = tmp_path / "snapshot.db"
    memory = Mnemosyne(db_path=db_path)
    writer_started = threading.Event()
    writer_done = threading.Event()
    writer_errors = []
    original_export = memory.beam.export_to_dict

    def export_then_pause():
        payload = original_export()
        writer_started.set()
        assert writer_done.wait(5), "concurrent writer did not finish"
        return payload

    def write_omitted_surface():
        assert writer_started.wait(5), "export did not reach its read snapshot"
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO working_memory
                        (id, content, timestamp, session_id, pinned)
                    VALUES ('late-memory', 'late write', '2026-01-01', 'default', 1)
                    """
                )
        except Exception as exc:  # pragma: no cover - surfaced below
            writer_errors.append(exc)
        finally:
            writer_done.set()

    monkeypatch.setattr(memory.beam, "export_to_dict", export_then_pause)
    writer = threading.Thread(target=write_omitted_surface)
    writer.start()
    output_path = tmp_path / "snapshot.json"
    result = memory.export_to_file(str(output_path))
    writer.join(5)

    assert not writer.is_alive()
    assert writer_errors == []
    assert memory.conn.execute("SELECT COUNT(*) FROM working_memory").fetchone()[0] == 1
    exported = json.loads(output_path.read_text(encoding="utf-8"))
    partial = {
        surface["section"]: surface
        for surface in exported["mnemosyne_export"]["completeness"]["partial_surfaces"]
    }
    assert exported["working_memory"] == []
    assert result["complete"] is True
    assert "working_memory" not in partial


def test_export_preserves_caller_owned_transaction(tmp_path):
    memory = Mnemosyne(db_path=tmp_path / "caller.db")
    memory.conn.execute("CREATE TABLE caller_marker (value TEXT NOT NULL)")
    memory.conn.commit()
    memory.conn.execute("INSERT INTO caller_marker VALUES ('uncommitted')")

    memory.export_to_file(str(tmp_path / "caller.json"))

    assert memory.conn.in_transaction is True
    assert memory.conn.execute("SELECT value FROM caller_marker").fetchone()[0] == "uncommitted"
    memory.conn.rollback()
    assert memory.conn.execute("SELECT COUNT(*) FROM caller_marker").fetchone()[0] == 0


def test_unknown_expression_default_counts_null_source_rows():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE working_memory (
            id TEXT PRIMARY KEY,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "INSERT INTO working_memory (id, generated_at) VALUES ('null-source', NULL)"
    )

    manifest = _export_completeness(conn, include_sync_events=False)
    partial = {surface["section"]: surface for surface in manifest["partial_surfaces"]}
    omitted = {
        field["field"]: field["affected_rows"]
        for field in partial["working_memory"]["omitted_fields"]
    }

    assert manifest["complete"] is False
    assert omitted["generated_at"] == 1
