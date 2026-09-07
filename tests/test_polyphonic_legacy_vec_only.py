"""Polyphonic recall must read the store populated by episodic vector writes."""
import json

import numpy as np
import pytest

import mnemosyne.core.beam as bm
from mnemosyne.core.polyphonic_recall import PolyphonicRecallEngine

pytest.importorskip("sqlite_vec")


@pytest.fixture(params=["float32", "int8", "bit"])
def store(request, tmp_path, monkeypatch):
    import sqlite_vec

    beam = bm.BeamMemory(session_id="vector-session", db_path=tmp_path / "memory.db")
    conn = beam.conn
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("DROP TABLE IF EXISTS vec_episodes")
    kind = request.param
    ddl = "float" if kind == "float32" else kind
    conn.execute(f"CREATE VIRTUAL TABLE vec_episodes USING vec0(embedding {ddl}[64])")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    query = np.array([1.0, -1.0] * 32, dtype=np.float32)
    query /= np.linalg.norm(query)
    monkeypatch.setattr(bm._embeddings, "available", lambda: True)
    monkeypatch.setattr(bm._embeddings, "embed_query", lambda text: query)
    monkeypatch.setattr(bm._embeddings, "embed", lambda texts: [query for _ in texts])
    monkeypatch.delenv("MNEMOSYNE_VOICE_VECTOR", raising=False)
    return beam, query, kind


def _write(beam, monkeypatch, vector, label, source="selected"):
    monkeypatch.setattr(bm._embeddings, "embed", lambda texts: [vector for _ in texts])
    mid = beam.consolidate_to_episodic(label, [], source=source)
    rowid = beam.conn.execute("SELECT rowid FROM episodic_memory WHERE id=?", (mid,)).fetchone()[0]
    assert beam.conn.execute("SELECT embedding FROM vec_episodes WHERE rowid=?", (rowid,)).fetchone()
    assert beam.conn.execute("SELECT 1 FROM memory_embeddings WHERE memory_id=?", (mid,)).fetchone() is None
    return mid, rowid


def _stored_cosine(beam, query, rowid, kind):
    blob = beam.conn.execute("SELECT embedding FROM vec_episodes WHERE rowid=?", (rowid,)).fetchone()[0]
    if kind == "bit":
        qblob = beam.conn.execute("SELECT vec_quantize_binary(?)", (json.dumps(query.tolist()),)).fetchone()[0]
        differences = np.unpackbits(np.bitwise_xor(np.frombuffer(qblob, dtype=np.uint8), np.frombuffer(blob, dtype=np.uint8))).sum()
        return float(np.cos(np.pi * differences / query.size))
    if kind == "int8":
        qblob = beam.conn.execute("SELECT vec_quantize_int8(?, 'unit')", (json.dumps(query.tolist()),)).fetchone()[0]
        q = np.frombuffer(qblob, dtype=np.int8).astype(np.float64)
        row = np.frombuffer(blob, dtype=np.int8).astype(np.float64)
    else:
        q = query.astype(np.float64)
        row = np.frombuffer(blob, dtype=np.float32).astype(np.float64)
    return float(np.dot(q, row) / (np.linalg.norm(q) * np.linalg.norm(row)))


def _voice(beam, query, **kwargs):
    return PolyphonicRecallEngine(db_path=beam.db_path, conn=beam.conn)._vector_voice(query, **kwargs)


def test_normal_episodic_write_without_json_recalled(store, monkeypatch):
    beam, query, kind = store
    mid, _ = _write(beam, monkeypatch, query, "persisted directional record")
    assert bm._classify_vec_store_regime(beam.conn) == "legacy"
    statements = []
    beam.conn.set_trace_callback(statements.append)
    results = _voice(beam, query)
    hit = next((r for r in results if r.memory_id == mid), None)
    assert hit is not None, "normal episodic writes must not disappear from the vector voice"
    assert hit.score == pytest.approx(1.0)
    assert hit.metadata["backend"] == "sqlite-vec"
    assert hit.metadata["vec_type"] == kind
    assert not any("MATCH" in sql and "vec_episodes" in sql for sql in statements)
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements)
    beam.conn.set_trace_callback(None)
    for voice in ("TEMPORAL", "GRAPH", "FACT"):
        monkeypatch.setenv(f"MNEMOSYNE_VOICE_{voice}", "0")
    monkeypatch.setenv("MNEMOSYNE_POLYPHONIC_RECALL", "1")
    monkeypatch.setattr(bm._embeddings, "embed", lambda texts: [query for _ in texts])
    public = beam.recall("unrelated opaque query", top_k=5)
    hit = next((r for r in public if r.get("id") == mid), None)
    assert hit is not None
    assert hit["voice_scores"]["vector"] > 0
    assert not any(hit["voice_scores"].get(voice) for voice in ("temporal", "graph", "fact"))


@pytest.mark.parametrize("filter_arg", ["source", "topic"])
def test_legacy_scan_filters_before_bounded_selection(store, monkeypatch, filter_arg):
    beam, query, kind = store
    # More perfect but ineligible hits than the normalized path's KNN budget.
    for i in range(65):
        mid, _ = _write(beam, monkeypatch, query, f"ineligible record {i}", source="other")
        if i % 3 == 0:
            beam.conn.execute("UPDATE episodic_memory SET source='selected', superseded_by='replacement' WHERE id=?", (mid,))
        elif i % 3 == 1:
            beam.conn.execute("UPDATE episodic_memory SET source='selected', valid_until='2000-01-01' WHERE id=?", (mid,))
        beam.conn.commit()
    target = query.copy()
    target[:4] *= -1
    mid, rowid = _write(beam, monkeypatch, target, "eligible survivor")
    results = _voice(beam, query, **{filter_arg: "selected"})
    assert [r.memory_id for r in results] == [mid]
    expected = _stored_cosine(beam, query, rowid, kind)
    assert results[0].metadata["cosine_similarity"] == pytest.approx(expected, abs=1e-6)
    assert results[0].score == pytest.approx((expected + 1) / 2, abs=1e-6)


@pytest.mark.parametrize("store", ["float32", "int8"], indirect=True)
def test_legacy_low_norm_target_beyond_knn_and_admission(store, monkeypatch):
    beam, query, kind = store
    for i in range(75):
        distractor = query.copy()
        distractor[:8] *= -1  # cosine .75: below the .80 admission boundary
        _write(beam, monkeypatch, distractor, f"directional distractor {i}")
    mid, rowid = _write(beam, monkeypatch, query, "small magnitude legacy target")
    # Ordinary writes normalize. Replace only this blob to model a pre-normalization row.
    low = query * 0.1
    value = json.dumps(low.tolist())
    expression = "vec_quantize_int8(?, 'unit')" if kind == "int8" else "?"
    beam.conn.execute("DELETE FROM vec_episodes WHERE rowid=?", (rowid,))
    beam.conn.execute(f"INSERT INTO vec_episodes(rowid, embedding) VALUES (?, {expression})", (rowid, value))
    beam.conn.commit()
    for i in range(75):
        _write(beam, monkeypatch, distractor, f"later directional distractor {i}")
    raw = beam.conn.execute("SELECT embedding FROM vec_episodes WHERE rowid=?", (rowid,)).fetchone()[0]
    dtype = np.int8 if kind == "int8" else np.float32
    assert 0 < np.linalg.norm(np.frombuffer(raw, dtype=dtype)) < (80 if kind == "int8" else 0.2)
    knn = beam.conn.execute(f"SELECT rowid FROM vec_episodes WHERE embedding MATCH {expression} AND k=60 ORDER BY distance", (json.dumps(query.tolist()),)).fetchall()
    assert rowid not in {r[0] for r in knn}
    assert beam.conn.execute("SELECT count(*) FROM memory_embeddings").fetchone()[0] == 0
    results = _voice(beam, query)
    assert [r.memory_id for r in results] == [mid]
    expected = _stored_cosine(beam, query, rowid, kind)
    assert results[0].score == pytest.approx((expected + 1) / 2, abs=1e-6)
    # Public routing/fusion/hydration, without another voice rescuing the target.
    for voice in ("TEMPORAL", "GRAPH", "FACT"):
        monkeypatch.setenv(f"MNEMOSYNE_VOICE_{voice}", "0")
    monkeypatch.setenv("MNEMOSYNE_POLYPHONIC_RECALL", "1")
    monkeypatch.setattr(bm._embeddings, "embed", lambda texts: [query for _ in texts])
    public = beam.recall("unrelated opaque query", top_k=5)
    hit = next((r for r in public if r.get("id") == mid), None)
    assert hit is not None
    assert hit["voice_scores"]["vector"] > 0
    assert not any(hit["voice_scores"].get(voice) for voice in ("temporal", "graph", "fact"))


def test_json_only_fallback_and_vec_authority(store, monkeypatch):
    beam, query, kind = store
    target = query.copy()
    target[:4] *= -1
    mid, rowid = _write(beam, monkeypatch, target, "dual representation record")
    below = query.copy()
    below[:16] *= -1
    rejected, _ = _write(beam, monkeypatch, below, "below admission record")
    json_mid, json_rowid = _write(beam, monkeypatch, query, "JSON only record")
    beam.conn.execute("DELETE FROM vec_episodes WHERE rowid=?", (json_rowid,))
    for key in (mid, rejected, json_mid):
        beam.conn.execute("INSERT INTO memory_embeddings(memory_id, embedding_json) VALUES (?,?)", (key, json.dumps(query.tolist())))
    beam.conn.commit()
    results = _voice(beam, query)
    ids = [r.memory_id for r in results]
    assert set(ids) == {mid, json_mid}
    assert len(ids) == 2
    hit = next(r for r in results if r.memory_id == mid)
    expected = _stored_cosine(beam, query, rowid, kind)
    assert hit.score == pytest.approx((expected + 1) / 2, abs=1e-6)
    assert hit.metadata["backend"] == "sqlite-vec"
    fallback = next(r for r in results if r.memory_id == json_mid)
    assert fallback.score == pytest.approx(1.0)
    assert fallback.metadata["backend"] == "memory_embeddings"


@pytest.mark.parametrize("regime", ["unknown", "legacy-sign", "read-error"])
def test_uncertain_marker_still_reads_vec_only_rows(store, monkeypatch, regime):
    beam, query, _ = store
    mid, _ = _write(beam, monkeypatch, query, "uncertain marker record")

    def classify(*args, **kwargs):
        if regime == "read-error":
            raise RuntimeError("marker unavailable")
        return regime

    monkeypatch.setattr(bm, "_classify_vec_store_regime", classify)
    assert [r.memory_id for r in _voice(beam, query)] == [mid]


def test_legacy_scan_failure_keeps_json_fallback(store, monkeypatch):
    import sqlite3

    beam, query, _ = store
    mid, _ = _write(beam, monkeypatch, query, "fallback availability record")
    beam.conn.execute("INSERT INTO memory_embeddings(memory_id, embedding_json) VALUES (?,?)", (mid, json.dumps(query.tolist())))
    beam.conn.commit()

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("unavailable scan")

    monkeypatch.setattr(PolyphonicRecallEngine, "_legacy_episodic_vector_voice", fail)
    results = _voice(beam, query)
    assert [r.memory_id for r in results] == [mid]
    assert results[0].metadata["backend"] == "memory_embeddings"
    assert results[0].score == pytest.approx(1.0)
