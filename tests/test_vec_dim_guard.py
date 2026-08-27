"""Tests for the sqlite-vec dimension-consistency guard in init_beam().

The dimension of a sqlite-vec ``vec0`` table is fixed at creation time. If a
database already stores vectors at one dimension and the process is later
configured (EMBEDDING_DIM) for a different one, creating a new ``vec0`` table at
that configured dimension is silently wrong: every insert of a real vector then
fails and recall reads an empty/incompatible index. init_beam() must detect the
established dimension from the database and refuse to create mismatched tables,
leaving existing tables untouched, rather than corrupting the store.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import sqlite3

import pytest

from mnemosyne.core.memory import Mnemosyne
import mnemosyne.core.beam as beam


def test_existing_vec_dim_none_on_fresh_db(tmp_path):
    """A database with no vec0 tables has no established dimension."""
    conn = beam._get_connection(Path(tmp_path) / "fresh.db")
    assert beam._existing_vec_dim(conn) is None


def test_existing_vec_dim_reads_declared_dimension(tmp_path):
    """The helper reads the dimension declared in a vec0 table's DDL."""
    if not beam._SQLITE_VEC_AVAILABLE:
        import pytest  # type: ignore

        pytest.skip("sqlite-vec unavailable")
    conn = beam._get_connection(Path(tmp_path) / "declared.db")
    conn.execute("CREATE VIRTUAL TABLE vec_episodes USING vec0(embedding int8[768])")
    conn.commit()
    assert beam._existing_vec_dim(conn) == 768


def test_init_beam_returns_frozen_fresh_status(tmp_path, monkeypatch):
    """Fresh databases report their configured dimension without a mismatch."""
    monkeypatch.setattr(beam, "EMBEDDING_DIM", 384)

    result = beam.init_beam(Path(tmp_path) / "fresh-status.db")

    assert result == beam.BeamInitResult(False, None, 384, ())
    with pytest.raises(FrozenInstanceError):
        setattr(result, "configured_dim", 768)
    assert result.stored_dims == ()
    with pytest.raises(FrozenInstanceError):
        setattr(result, "stored_dims", (("vec_episodes", 768),))


def test_init_beam_skips_vec_creation_on_dim_mismatch(tmp_path, monkeypatch):
    """A mismatch preserves tables and skips later vector-table backfill/creation."""
    if not beam._SQLITE_VEC_AVAILABLE:
        import pytest  # type: ignore

        pytest.skip("sqlite-vec unavailable")

    db = Path(tmp_path) / "store.db"

    # Initialize the store at dimension 768.
    monkeypatch.setattr(beam, "EMBEDDING_DIM", 768)
    initial = beam.init_beam(db)
    assert initial == beam.BeamInitResult(False, None, 768, ())
    conn = beam._get_connection(db)

    # Simulate a database written before vec_working and vec_facts existed.
    conn.execute("DROP TABLE IF EXISTS vec_working")
    conn.execute("DROP TABLE IF EXISTS vec_facts")
    conn.commit()
    assert beam._existing_vec_dim(conn) == 768

    # Reopen configured for a DIFFERENT dimension (the misconfiguration).
    monkeypatch.setattr(beam, "EMBEDDING_DIM", 384)
    backfill_calls = []
    monkeypatch.setattr(
        beam,
        "_backfill_vec_working_from_memory_embeddings",
        lambda backfill_conn: backfill_calls.append(backfill_conn),
    )
    result = beam.init_beam(db)

    assert result == beam.BeamInitResult(True, 768, 384, (("vec_episodes", 768),))
    assert backfill_calls == []

    # The guard must NOT have created either missing table at the wrong dimension.
    assert conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'vec_working'"
    ).fetchone() is None
    assert conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'vec_facts'"
    ).fetchone() is None

    # The existing data table is left untouched at its real dimension.
    ep = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'vec_episodes'"
    ).fetchone()
    assert ep is not None and "[768]" in ep[0]


def test_init_beam_reports_mismatch_when_sqlite_vec_is_unavailable(tmp_path, monkeypatch):
    """Status reads an existing vec0 dimension even without sqlite-vec available."""
    if not beam._SQLITE_VEC_AVAILABLE:
        pytest.skip("sqlite-vec unavailable")

    db = Path(tmp_path) / "unavailable-status.db"
    monkeypatch.setattr(beam, "EMBEDDING_DIM", 768)
    beam.init_beam(db)
    conn = beam._get_connection(db)
    conn.execute("DROP TABLE IF EXISTS vec_working")
    conn.execute("DROP TABLE IF EXISTS vec_facts")
    conn.commit()

    monkeypatch.setattr(beam, "EMBEDDING_DIM", 384)
    monkeypatch.setattr(beam, "_SQLITE_VEC_AVAILABLE", False)
    backfill_calls = []
    monkeypatch.setattr(
        beam,
        "_backfill_vec_working_from_memory_embeddings",
        lambda backfill_conn: backfill_calls.append(backfill_conn),
    )

    result = beam.init_beam(db)

    assert result == beam.BeamInitResult(True, 768, 384, (("vec_episodes", 768),))
    assert backfill_calls == []
    assert conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'vec_working'"
    ).fetchone() is None
    assert conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'vec_facts'"
    ).fetchone() is None


def test_init_beam_creates_vec_tables_when_dim_matches(tmp_path, monkeypatch):
    """A fresh configured database creates vec tables normally."""
    if not beam._SQLITE_VEC_AVAILABLE:
        import pytest  # type: ignore

        pytest.skip("sqlite-vec unavailable")

    db = Path(tmp_path) / "match.db"
    monkeypatch.setattr(beam, "EMBEDDING_DIM", 768)
    result = beam.init_beam(db)
    conn = beam._get_connection(db)

    working = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'vec_working'"
    ).fetchone()
    assert working is not None and "[768]" in working[0]
    assert result == beam.BeamInitResult(False, None, 768, ())


def test_init_beam_reports_match_and_constructor_preserves_status(tmp_path, monkeypatch):
    """Existing matching vec tables and normal construction expose status."""
    if not beam._SQLITE_VEC_AVAILABLE:
        import pytest  # type: ignore

        pytest.skip("sqlite-vec unavailable")

    db = Path(tmp_path) / "constructor-status.db"
    monkeypatch.setattr(beam, "EMBEDDING_DIM", 768)
    beam.init_beam(db)  # Ignoring the new result remains compatible.

    result = beam.init_beam(db)
    memory = beam.BeamMemory(session_id="status", db_path=db)

    assert result == beam.BeamInitResult(
        False,
        768,
        768,
        (("vec_episodes", 768), ("vec_working", 768), ("vec_facts", 768)),
    )
    assert memory.init_result == result


def test_mnemosyne_constructor_exposes_beam_init_status(tmp_path, monkeypatch):
    """The regular Mnemosyne API exposes the status produced by its BeamMemory."""
    monkeypatch.setattr(beam, "EMBEDDING_DIM", 384)

    memory = Mnemosyne(session_id="status", db_path=tmp_path / "mnemosyne.db")

    assert memory.init_result == memory.beam.init_result
    expected_existing_dim = 384 if beam._SQLITE_VEC_AVAILABLE else None
    expected_stored_dims = (
        (("vec_episodes", 384), ("vec_working", 384), ("vec_facts", 384))
        if beam._SQLITE_VEC_AVAILABLE
        else ()
    )
    assert memory.init_result == beam.BeamInitResult(
        False, expected_existing_dim, 384, expected_stored_dims
    )


def test_ignored_mismatch_result_recovers_after_matching_reinit(tmp_path, monkeypatch):
    """Ignoring a mismatch result does not block recovery after configuration repair."""
    if not beam._SQLITE_VEC_AVAILABLE:
        import pytest  # type: ignore

        pytest.skip("sqlite-vec unavailable")

    db = Path(tmp_path) / "recovery.db"
    monkeypatch.setattr(beam, "EMBEDDING_DIM", 768)
    beam.init_beam(db)

    monkeypatch.setattr(beam, "EMBEDDING_DIM", 384)
    beam.init_beam(db)  # Historical ignored-return call pattern.

    monkeypatch.setattr(beam, "EMBEDDING_DIM", 768)
    recovered = beam.init_beam(db)
    assert recovered == beam.BeamInitResult(
        False,
        768,
        768,
        (("vec_episodes", 768), ("vec_working", 768), ("vec_facts", 768)),
    )


def test_dim_mismatch_message_is_self_healing():
    """The mismatch message says it is not corruption and gives recovery commands."""
    msg = beam._dim_mismatch_message((("vec_episodes", 768),), configured_dim=384)
    low = msg.lower()
    assert "not database corruption" in low, msg
    assert "mnemosyne reindex" in low, msg
    assert "mnemosyne_embedding_dim=" in low, msg
    assert "768" in msg and "384" in msg


@pytest.mark.parametrize(
    "creation_order",
    [
        ("vec_episodes", "vec_working", "vec_facts"),
        ("vec_facts", "vec_working", "vec_episodes"),
    ],
)
def test_init_beam_reports_mixed_stored_dims_without_sqlite_vec(
    tmp_path, monkeypatch, caplog, creation_order
):
    """Mixed legacy indexes are order-independent and never gain new vec tables."""
    db = Path(tmp_path) / "mixed-no-extension.db"
    conn = beam._get_connection(db)
    dimensions = {"vec_episodes": 768, "vec_working": 384, "vec_facts": 384}
    for table in creation_order:
        conn.execute(f"CREATE TABLE {table} (embedding int8[{dimensions[table]}])")
    conn.commit()
    before = dict(
        conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE name IN "
            "('vec_episodes', 'vec_working', 'vec_facts')"
        )
    )

    monkeypatch.setattr(beam, "EMBEDDING_DIM", 384)
    monkeypatch.setattr(beam, "_SQLITE_VEC_AVAILABLE", False)
    backfill_calls = []
    monkeypatch.setattr(
        beam,
        "_backfill_vec_working_from_memory_embeddings",
        lambda backfill_conn: backfill_calls.append(backfill_conn),
    )

    result = beam.init_beam(db)

    assert result == beam.BeamInitResult(
        True,
        None,
        384,
        (("vec_episodes", 768), ("vec_working", 384), ("vec_facts", 384)),
    )
    assert backfill_calls == []
    assert dict(
        conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE name IN "
            "('vec_episodes', 'vec_working', 'vec_facts')"
        )
    ) == before
    assert "mixed vector-index dimensions" in caplog.text
    assert "vec_episodes=768, vec_working=384, vec_facts=384" in caplog.text


def test_legacy_vec_episodes_rejects_configured_dimension_query(tmp_path):
    """The mixed-state status protects the real sqlite-vec failure boundary."""
    if not beam._SQLITE_VEC_AVAILABLE:
        pytest.skip("sqlite-vec unavailable")

    conn = beam._get_connection(Path(tmp_path) / "legacy-query.db")
    conn.execute("CREATE VIRTUAL TABLE vec_episodes USING vec0(embedding int8[768])")

    with pytest.raises(sqlite3.OperationalError, match="[Dd]imension mismatch"):
        conn.execute(
            "SELECT rowid FROM vec_episodes "
            "WHERE embedding MATCH vec_quantize_int8(?, 'unit') AND k=1",
            (json.dumps([0.0] * 384),),
        ).fetchall()



class _BinaryBits(list):
    """List-compatible boolean result for the binary-vector path."""

    def astype(self, _dtype):
        return self


class _QueryVector(list):
    """Dependency-free 1D numeric-array stand-in for public recall."""

    @property
    def shape(self):
        return (len(self),)

    def __getitem__(self, index):
        item = super().__getitem__(index)
        return type(self)(item) if isinstance(index, slice) else item

    def __gt__(self, value):
        return _BinaryBits(item > value for item in self)

    def flatten(self):
        return self

    def tolist(self):
        return list(self)


def _seed_legacy_episodic_vec_store(tmp_path, monkeypatch):
    """Create a populated 768-dim vec index before reopening it at 384 dims."""
    if not beam._SQLITE_VEC_AVAILABLE:
        pytest.skip("sqlite-vec unavailable")

    db = Path(tmp_path) / "legacy-recall.db"
    monkeypatch.setattr(beam, "EMBEDDING_DIM", 768)
    writer = beam.BeamMemory(session_id="legacy", db_path=db)
    assert beam._vec_available(writer.conn)
    writer.conn.execute(
        "INSERT INTO episodic_memory (id, content, source, timestamp, importance) "
        "VALUES ('legacy-episode', 'legacy vector fallback marker', 'test', "
        "datetime('now'), 0.5)"
    )
    rowid = writer.conn.execute(
        "SELECT rowid FROM episodic_memory WHERE id = 'legacy-episode'"
    ).fetchone()[0]
    writer.conn.execute(
        "INSERT INTO vec_episodes(rowid, embedding) "
        "VALUES (?, vec_quantize_int8(?, 'unit'))",
        (rowid, json.dumps([0.0] * 768)),
    )
    writer.conn.commit()
    monkeypatch.setattr(beam, "EMBEDDING_DIM", 384)
    return db


@pytest.mark.parametrize("memory_class", [beam.BeamMemory, Mnemosyne])
def test_public_linear_recall_skips_legacy_vec_search_and_returns_fts_match(
    tmp_path, monkeypatch, memory_class
):
    """Both public linear APIs avoid a 768→384 sqlite-vec query but retain FTS."""
    db = _seed_legacy_episodic_vec_store(tmp_path, monkeypatch)
    monkeypatch.setenv("MNEMOSYNE_ENHANCED_RECALL", "0")
    monkeypatch.setenv("MNEMOSYNE_POLYPHONIC_RECALL", "0")
    before_dims = beam._existing_vec_dims(beam._get_connection(db))
    before_vec_rows = beam._get_connection(db).execute(
        "SELECT rowid FROM vec_episodes ORDER BY rowid"
    ).fetchall()
    calls = []
    monkeypatch.setattr(beam._embeddings, "available", lambda: True)
    monkeypatch.setattr(
        beam._embeddings, "embed_query", lambda _query: _QueryVector([0.0] * 384)
    )
    def unexpected_vec_search(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("unexpected sqlite-vec episodic search")

    monkeypatch.setattr(beam, "_episodic_vec_search_scoped", unexpected_vec_search)

    memory = memory_class(session_id="legacy", db_path=db)
    results = memory.recall("legacy vector fallback marker", top_k=5)

    beam_memory = memory if memory_class is beam.BeamMemory else memory.beam
    assert beam_memory.init_result.vec_dim_mismatch
    assert any(result["id"] == "legacy-episode" for result in results)
    assert calls == []
    assert beam._existing_vec_dims(beam_memory.conn) == before_dims
    assert beam_memory.conn.execute(
        "SELECT rowid FROM vec_episodes ORDER BY rowid"
    ).fetchall() == before_vec_rows


def test_public_linear_recall_uses_compatible_episodic_vec_despite_mixed_indexes(
    tmp_path, monkeypatch
):
    """A non-episodic mismatch does not disable compatible episodic sqlite-vec."""
    if not beam._SQLITE_VEC_AVAILABLE:
        pytest.skip("sqlite-vec unavailable")

    monkeypatch.setenv("MNEMOSYNE_ENHANCED_RECALL", "0")
    monkeypatch.setenv("MNEMOSYNE_POLYPHONIC_RECALL", "0")
    monkeypatch.setattr(beam, "EMBEDDING_DIM", 384)
    db = Path(tmp_path) / "mixed-episodic-compatible.db"
    writer = beam.BeamMemory(session_id="mixed", db_path=db)
    writer.conn.execute(
        "INSERT INTO episodic_memory (id, content, source, timestamp, importance) "
        "VALUES ('semantic-episode', 'calibrated nebula archive', 'test', "
        "datetime('now'), 0.5)"
    )
    rowid = writer.conn.execute(
        "SELECT rowid FROM episodic_memory WHERE id = 'semantic-episode'"
    ).fetchone()[0]
    writer.conn.execute(
        "INSERT INTO vec_episodes(rowid, embedding) "
        "VALUES (?, vec_quantize_int8(?, 'unit'))",
        (rowid, json.dumps([0.0] * 384)),
    )
    writer.conn.execute("DROP TABLE vec_working")
    writer.conn.execute("CREATE VIRTUAL TABLE vec_working USING vec0(embedding int8[768])")
    writer.conn.commit()

    calls = []
    original_vec_search = beam._episodic_vec_search_scoped
    monkeypatch.setattr(beam._embeddings, "available", lambda: True)
    monkeypatch.setattr(
        beam._embeddings, "embed_query", lambda _query: _QueryVector([0.0] * 384)
    )

    def traced_vec_search(*args, **kwargs):
        calls.append(True)
        return original_vec_search(*args, **kwargs)

    monkeypatch.setattr(beam, "_episodic_vec_search_scoped", traced_vec_search)
    memory = beam.BeamMemory(session_id="mixed", db_path=db)

    results = memory.recall("unrelated aurora question", top_k=5)

    assert memory.init_result.vec_dim_mismatch
    assert memory.init_result.stored_dims == (
        ("vec_episodes", 384), ("vec_working", 768), ("vec_facts", 384)
    )
    assert calls == [True]
    assert [result["id"] for result in results] == ["semantic-episode"]


def test_public_linear_recall_uses_query_dim_when_module_constant_differs(tmp_path, monkeypatch):
    """Compatible episodic sqlite-vec stays on when only beam.EMBEDDING_DIM drifted."""
    if not beam._SQLITE_VEC_AVAILABLE:
        pytest.skip("sqlite-vec unavailable")

    monkeypatch.setenv("MNEMOSYNE_ENHANCED_RECALL", "0")
    monkeypatch.setenv("MNEMOSYNE_POLYPHONIC_RECALL", "0")
    monkeypatch.setattr(beam, "EMBEDDING_DIM", 384)
    db = Path(tmp_path) / "query-dim.db"
    writer = beam.BeamMemory(session_id="query-dim", db_path=db)
    writer.conn.execute(
        "INSERT INTO episodic_memory (id, content, source, timestamp, importance) "
        "VALUES ('semantic-episode', 'calibrated nebula archive', 'test', "
        "datetime('now'), 0.5)"
    )
    rowid = writer.conn.execute(
        "SELECT rowid FROM episodic_memory WHERE id = 'semantic-episode'"
    ).fetchone()[0]
    writer.conn.execute(
        "INSERT INTO vec_episodes(rowid, embedding) "
        "VALUES (?, vec_quantize_int8(?, 'unit'))",
        (rowid, json.dumps([0.0] * 384)),
    )
    writer.conn.commit()
    monkeypatch.setattr(beam, "EMBEDDING_DIM", 768)

    calls = []
    original_vec_search = beam._episodic_vec_search_scoped
    monkeypatch.setattr(beam._embeddings, "available", lambda: True)
    monkeypatch.setattr(
        beam._embeddings, "embed_query", lambda _query: _QueryVector([0.0] * 384)
    )

    def traced_vec_search(*args, **kwargs):
        calls.append(True)
        return original_vec_search(*args, **kwargs)

    monkeypatch.setattr(beam, "_episodic_vec_search_scoped", traced_vec_search)
    memory = beam.BeamMemory(session_id="query-dim", db_path=db)

    results = memory.recall("unrelated aurora question", top_k=5)

    assert calls == [True]
    assert [result["id"] for result in results] == ["semantic-episode"]


def test_public_linear_recall_normalizes_single_row_query_and_rejects_batches(tmp_path, monkeypatch):
    """sqlite-vec receives exactly one flat query vector, never a batch."""
    if not beam._SQLITE_VEC_AVAILABLE:
        pytest.skip("sqlite-vec unavailable")
    import numpy as np

    monkeypatch.setenv("MNEMOSYNE_ENHANCED_RECALL", "0")
    monkeypatch.setenv("MNEMOSYNE_POLYPHONIC_RECALL", "0")
    monkeypatch.setattr(beam, "EMBEDDING_DIM", 384)
    memory = beam.BeamMemory(session_id="shape", db_path=Path(tmp_path) / "shape.db")
    memory.conn.execute(
        "INSERT INTO episodic_memory (id, content, source, timestamp, importance) "
        "VALUES ('shape-target', 'shape fallback marker', 'test', datetime('now'), 0.5)"
    )
    rowid = memory.conn.execute(
        "SELECT rowid FROM episodic_memory WHERE id = 'shape-target'"
    ).fetchone()[0]
    memory.conn.execute(
        "INSERT INTO vec_episodes(rowid, embedding) VALUES (?, vec_quantize_int8(?, 'unit'))",
        (rowid, json.dumps([0.0] * 384)),
    )
    memory.conn.commit()
    monkeypatch.setattr(beam._embeddings, "available", lambda: True)
    original_vec_search = beam._episodic_vec_search_scoped
    calls = []

    def traced_vec_search(*args, **kwargs):
        calls.append(args[1])
        return original_vec_search(*args, **kwargs)

    monkeypatch.setattr(beam, "_episodic_vec_search_scoped", traced_vec_search)
    monkeypatch.setattr(
        beam._embeddings, "embed_query", lambda _query: np.zeros((1, 384), dtype=np.float32)
    )
    assert [result["id"] for result in memory.recall("no lexical match", top_k=1)] == ["shape-target"]
    assert len(calls) == 1 and len(calls[0]) == 384

    calls.clear()
    monkeypatch.setattr(
        beam._embeddings, "embed_query", lambda _query: np.zeros((2, 384), dtype=np.float32)
    )
    assert memory.recall("shape fallback marker", top_k=1)[0]["id"] == "shape-target"
    assert calls == []

    calls.clear()
    monkeypatch.setattr(
        beam._embeddings, "embed_query", lambda _query: np.zeros(768, dtype=np.float32)
    )
    assert memory.recall("shape fallback marker", top_k=1)[0]["id"] == "shape-target"
    assert calls == []


def test_public_linear_recall_uses_vec_search_when_dimensions_match(tmp_path, monkeypatch):
    """The mismatch guard does not disable the normal sqlite-vec episodic path."""
    if not beam._SQLITE_VEC_AVAILABLE:
        pytest.skip("sqlite-vec unavailable")

    monkeypatch.setenv("MNEMOSYNE_ENHANCED_RECALL", "0")
    monkeypatch.setenv("MNEMOSYNE_POLYPHONIC_RECALL", "0")
    monkeypatch.setattr(beam, "EMBEDDING_DIM", 384)
    memory = beam.BeamMemory(session_id="matched", db_path=Path(tmp_path) / "matched.db")
    calls = []
    monkeypatch.setattr(beam._embeddings, "available", lambda: True)
    monkeypatch.setattr(
        beam._embeddings, "embed_query", lambda _query: _QueryVector([0.0] * 384)
    )
    monkeypatch.setattr(
        beam, "_episodic_vec_search_scoped", lambda *_args, **_kwargs: calls.append(True) or []
    )

    memory.recall("normal vector path", top_k=5)

    assert not memory.init_result.vec_dim_mismatch
    assert calls == [True]


def test_public_recall_scopes_in_memory_fallback_before_bounded_candidates(tmp_path, monkeypatch):
    """Public recall preserves an in-scope semantic match after 10,000 foreign rows."""
    class Vector(list):
        def __truediv__(self, _norm):
            return self

    class Numpy:
        float32 = object()

        class linalg:
            @staticmethod
            def norm(vector):
                return 1.0 if any(vector) else 0.0

        @staticmethod
        def array(vector, dtype):
            assert dtype is Numpy.float32
            return Vector(vector)

        @staticmethod
        def dot(left, right):
            return sum(a * b for a, b in zip(left, right))

    monkeypatch.setenv("MNEMOSYNE_POLYPHONIC_RECALL", "0")
    monkeypatch.setenv("MNEMOSYNE_ENHANCED_RECALL", "0")
    monkeypatch.setattr(beam, "np", Numpy)
    memory = beam.BeamMemory(session_id="target-session", db_path=Path(tmp_path) / "scope.db")
    memory.init_result = beam.BeamInitResult(True, 768, 384, (("vec_episodes", 768),))
    monkeypatch.setattr(beam, "_existing_vec_dims", lambda _conn: (("vec_episodes", 768),))
    monkeypatch.setattr(beam._embeddings, "available", lambda: True)
    monkeypatch.setattr(
        beam._embeddings,
        "embed_query",
        lambda _query: Numpy.array([1.0], dtype=Numpy.float32),
    )
    monkeypatch.setattr(beam, "_wm_vec_search", lambda conn, emb, k=20, **_kwargs: [])
    monkeypatch.setattr(beam, "_fts_search_working", lambda conn, query, k=20: [])
    monkeypatch.setattr(beam, "_fts_search", lambda conn, query, k=20: [])
    monkeypatch.setattr(beam, "_mib", None)
    conn = memory.conn

    out_of_scope = [
        (f"other-{index}", "unrelated text", "test", "other-session")
        for index in range(10_000)
    ]
    conn.executemany(
        "INSERT INTO episodic_memory (id, content, source, timestamp, session_id, scope) "
        "VALUES (?, ?, ?, datetime('now'), ?, 'session')",
        out_of_scope,
    )
    conn.executemany(
        "INSERT INTO memory_embeddings (memory_id, embedding_json) VALUES (?, ?)",
        [(memory_id, "[0.0]") for memory_id, *_ in out_of_scope],
    )
    conn.execute(
        "INSERT INTO episodic_memory (id, content, source, timestamp, session_id) "
        "VALUES ('target', 'different wording', 'test', datetime('now'), 'target-session')"
    )
    conn.execute(
        "INSERT INTO memory_embeddings (memory_id, embedding_json) VALUES ('target', '[1.0]')"
    )
    conn.commit()

    results = memory.recall("quasar signal", top_k=1)

    assert memory.init_result.vec_dim_mismatch
    assert [result["id"] for result in results] == ["target"]


def test_public_recall_scopes_sqlite_vec_candidates_before_knn_limit(tmp_path, monkeypatch):
    """An in-scope episodic vector survives 20 closer foreign KNN rows."""
    if not beam._SQLITE_VEC_AVAILABLE:
        pytest.skip("sqlite-vec unavailable")

    monkeypatch.setenv("MNEMOSYNE_ENHANCED_RECALL", "0")
    monkeypatch.setenv("MNEMOSYNE_POLYPHONIC_RECALL", "0")
    monkeypatch.setattr(beam, "EMBEDDING_DIM", 384)
    memory = beam.BeamMemory(session_id="target-session", db_path=Path(tmp_path) / "vec-scope.db")
    monkeypatch.setattr(beam._embeddings, "available", lambda: True)
    monkeypatch.setattr(
        beam._embeddings, "embed_query", lambda _query: _QueryVector([1.0] + [0.0] * 383)
    )
    monkeypatch.setattr(beam, "_episodic_fts_search_scoped", lambda *_args, **_kwargs: [])

    foreign = [
        (f"foreign-{index}", "foreign vector distractor", "foreign-session")
        for index in range(20)
    ]
    memory.conn.executemany(
        "INSERT INTO episodic_memory (id, content, source, timestamp, session_id, scope) "
        "VALUES (?, ?, 'test', datetime('now'), ?, 'session')",
        foreign,
    )
    memory.conn.execute(
        "INSERT INTO episodic_memory (id, content, source, timestamp, session_id, scope) "
        "VALUES ('target', 'target vector marker', 'test', datetime('now'), "
        "'target-session', 'session')"
    )
    foreign_rowids = memory.conn.execute(
        "SELECT rowid FROM episodic_memory WHERE session_id = 'foreign-session' ORDER BY rowid"
    ).fetchall()
    target_rowid = memory.conn.execute(
        "SELECT rowid FROM episodic_memory WHERE id = 'target'"
    ).fetchone()[0]
    memory.conn.executemany(
        "INSERT INTO vec_episodes(rowid, embedding) VALUES (?, vec_quantize_int8(?, 'unit'))",
        [(row[0], json.dumps([1.0] + [0.0] * 383)) for row in foreign_rowids],
    )
    memory.conn.execute(
        "INSERT INTO vec_episodes(rowid, embedding) VALUES (?, vec_quantize_int8(?, 'unit'))",
        (target_rowid, json.dumps([0.0, 1.0] + [0.0] * 382)),
    )
    memory.conn.commit()

    results = memory.recall("target vector marker", top_k=1)

    assert [result["id"] for result in results] == ["target"]
    assert all(result["id"] not in {row[0] for row in foreign} for result in results)


def test_public_recall_scopes_fts_candidates_before_limit_on_vec_dim_mismatch(tmp_path, monkeypatch):
    """An in-scope FTS match survives 20 foreign matches without JSON fallback."""
    db = _seed_legacy_episodic_vec_store(tmp_path, monkeypatch)
    monkeypatch.setenv("MNEMOSYNE_ENHANCED_RECALL", "0")
    monkeypatch.setenv("MNEMOSYNE_POLYPHONIC_RECALL", "0")
    memory = beam.BeamMemory(session_id="target-session", db_path=db)
    memory.conn.execute("DELETE FROM episodic_memory WHERE id = 'legacy-episode'")
    foreign = [
        (f"foreign-{index}", "scoped fts marker", "foreign-session")
        for index in range(20)
    ]
    memory.conn.executemany(
        "INSERT INTO episodic_memory (id, content, source, timestamp, session_id, scope) "
        "VALUES (?, ?, 'test', datetime('now'), ?, 'session')",
        foreign,
    )
    memory.conn.execute(
        "INSERT INTO episodic_memory (id, content, source, timestamp, session_id, scope) "
        "VALUES ('target', 'scoped fts marker', 'test', datetime('now'), "
        "'target-session', 'session')"
    )
    memory.conn.commit()
    monkeypatch.setattr(beam._embeddings, "available", lambda: True)
    monkeypatch.setattr(
        beam._embeddings, "embed_query", lambda _query: _QueryVector([0.0] * 384)
    )

    results = memory.recall("scoped fts marker", top_k=1)

    assert memory.init_result.vec_dim_mismatch
    assert [result["id"] for result in results] == ["target"]
    assert all(result["id"] not in {row[0] for row in foreign} for result in results)
