"""Recall returns persisted metadata without per-item consumer lookups (#665)."""

from mnemosyne.core.beam import BeamMemory


def test_recall_returns_working_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOSYNE_NO_EMBEDDINGS", "1")
    beam = BeamMemory(db_path=tmp_path / "working.db", session_id="s1")
    memory_id = beam.remember(
        "Bell prefers local-first infrastructure",
        metadata={"owner": "bell", "project": "alpha"},
    )

    result = next(item for item in beam.recall("local-first infrastructure") if item["id"] == memory_id)

    assert result["metadata"] == {"owner": "bell", "project": "alpha"}


def test_recall_returns_episodic_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOSYNE_NO_EMBEDDINGS", "1")
    beam = BeamMemory(db_path=tmp_path / "episodic.db", session_id="s1")
    memory_id = beam.consolidate_to_episodic(
        "Bell chose SQLite for the alpha project",
        [],
        metadata={"owner": "bell", "project": "alpha", "derived": True},
    )

    result = next(item for item in beam.recall("SQLite alpha project") if item["id"] == memory_id)

    assert result["metadata"] == {
        "owner": "bell",
        "project": "alpha",
        "derived": True,
    }


def test_recall_invalid_or_non_object_metadata_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOSYNE_NO_EMBEDDINGS", "1")
    beam = BeamMemory(db_path=tmp_path / "invalid.db", session_id="s1")
    invalid_id = beam.remember("invalid metadata sentinel")
    list_id = beam.remember("list metadata sentinel")
    beam.conn.execute(
        "UPDATE working_memory SET metadata_json = ? WHERE id = ?",
        ("{not-json", invalid_id),
    )
    beam.conn.execute(
        "UPDATE working_memory SET metadata_json = ? WHERE id = ?",
        ('["not", "an", "object"]', list_id),
    )
    beam.conn.commit()

    invalid = next(item for item in beam.recall("invalid metadata sentinel") if item["id"] == invalid_id)
    non_object = next(item for item in beam.recall("list metadata sentinel") if item["id"] == list_id)

    assert invalid["metadata"] == {}
    assert non_object["metadata"] == {}


def test_metadata_hydration_preserves_order_and_batches_queries(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOSYNE_NO_EMBEDDINGS", "1")
    beam = BeamMemory(db_path=tmp_path / "order.db", session_id="s1")
    beam.remember("alpha ranking exact", importance=0.9, metadata={"rank": 1})
    beam.remember("alpha ranking partial", importance=0.3, metadata={"rank": 2})
    beam.consolidate_to_episodic(
        "alpha ranking episodic",
        [],
        metadata={"rank": 3},
    )
    queries = []
    beam.conn.set_trace_callback(queries.append)

    results = beam.recall("alpha ranking")
    before = [(item["id"], item["score"]) for item in results]
    beam._attach_recall_metadata(results)

    assert [(item["id"], item["score"]) for item in results] == before
    assert all(isinstance(item["metadata"], dict) for item in results)
    metadata_queries = [
        query for query in queries
        if "SELECT id, metadata_json" in query
    ]
    assert len(metadata_queries) <= 4  # two recall passes, at most two tiers each
