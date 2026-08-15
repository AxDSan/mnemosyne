"""Public regression coverage for #726 batch transaction ownership."""

from pathlib import Path

from mnemosyne.batch_tool import apply_beam_batch, validate_batch_operations
from mnemosyne.core.beam import BeamMemory


def _beam(tmp_path):
    return BeamMemory(
        session_id="batch-owner-726", db_path=Path(tmp_path) / "batch-owner.db"
    )


def _count_content(conn, content):
    return conn.execute(
        "SELECT COUNT(*) FROM working_memory WHERE content = ?", (content,)
    ).fetchone()[0]


def test_caller_owned_batch_releases_savepoint_and_outer_rollback_removes_batch_writes(
    tmp_path,
):
    beam = _beam(tmp_path)
    existing_id = beam.remember("#726 before update")

    beam.conn.execute("CREATE TABLE caller_marker (value TEXT NOT NULL)")
    beam.conn.commit()
    beam.conn.execute("INSERT INTO caller_marker VALUES ('outer marker')")

    result = apply_beam_batch(
        beam,
        validate_batch_operations(
            [
                {"action": "remember", "content": "#726 remembered in batch"},
                {
                    "action": "update",
                    "memory_id": existing_id,
                    "content": "#726 updated in batch",
                },
            ]
        ),
    )

    assert result["status"] == "ok"
    assert beam.conn.in_transaction is True
    assert (
        beam.conn.execute("SELECT value FROM caller_marker").fetchone()[0]
        == "outer marker"
    )

    beam.conn.rollback()
    assert _count_content(beam.conn, "#726 remembered in batch") == 0
    assert beam.get(existing_id)["content"] == "#726 before update"
    assert beam.conn.execute("SELECT COUNT(*) FROM caller_marker").fetchone()[0] == 0


def test_failed_batch_rolls_back_its_savepoint_without_events_or_caller_data_loss(
    tmp_path,
):
    beam = _beam(tmp_path)
    events = []

    beam.conn.execute("CREATE TABLE caller_marker (value TEXT NOT NULL)")
    beam.conn.commit()
    beam.conn.execute("INSERT INTO caller_marker VALUES ('survives')")

    result = apply_beam_batch(
        beam,
        validate_batch_operations(
            [
                {"action": "remember", "content": "#726 must roll back"},
                {
                    "action": "update",
                    "memory_id": "missing",
                    "content": "never written",
                },
            ]
        ),
        audit_event=lambda name, **kwargs: events.append((name, kwargs)),
    )

    assert result["status"] == "error"
    assert result["failed_index"] == 1
    assert events == []
    assert beam.conn.in_transaction is True
    assert (
        beam.conn.execute("SELECT value FROM caller_marker").fetchone()[0] == "survives"
    )
    assert _count_content(beam.conn, "#726 must roll back") == 0

    beam.conn.rollback()
    assert beam.conn.execute("SELECT COUNT(*) FROM caller_marker").fetchone()[0] == 0
