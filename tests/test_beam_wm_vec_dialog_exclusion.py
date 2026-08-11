"""
Regression tests for the working-memory vector pool dialog flood (#696).

Pre-fix: raw dialog rows (source='conversation', legacy honcho imports)
dominate the nearest-N vector pool for conversational queries. A distilled
fact that is semantically close but lexically weak can rank beyond the pool
(and beyond the k slice of the compatibility scan) and never receive a dense
voice — recall either omits it or returns it with dense_score=0.0, and
downstream prefetch filters drop it.

Post-fix: the working-memory DENSE candidate pool excludes raw dialog
sources while the FTS path is untouched — dialog stays recallable
("what did we discuss") but can no longer starve facts out of the pool.
An explicit source=/topic= filter keeps the caller in control.
"""

import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from mnemosyne.core import beam as beam_module
from mnemosyne.core.beam import BeamMemory, _wm_vec_search

EMBEDDING_DIM = beam_module.EMBEDDING_DIM

# "kuma" is the one real token the fact shares with the query — exactly the
# production shape from #696: enough lexical overlap to pass the relevance
# gate (0.5 >= 0.15 for a 2-token query) but far too weak to surface the
# fact on its own. "alpha" matches nothing, keeping FTS results driven by
# the dialog flood.
QUERY = "kuma alpha"
FACT_ID = "fact-cli-rule"


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test.db"


def _unit_query_vec():
    return np.array([1.0] + [0.0] * (EMBEDDING_DIM - 1), dtype=np.float32)


def _near_vec(query, seed, noise=0.01):
    """Deterministic vector very close to the query (sim ~0.9999)."""
    rng = np.random.RandomState(seed)
    v = query + rng.randn(EMBEDDING_DIM).astype(np.float32) * noise
    return v / np.linalg.norm(v)


def _far_vec(query, theta=0.35):
    """Deterministic vector close to the query but clearly farther (sim ~0.94)."""
    orth = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    orth[1] = 1.0
    v = np.cos(theta) * query + np.sin(theta) * orth
    return v / np.linalg.norm(v)


def _flood_db(beam, dialog_count=560):
    """Seed dialog_count raw dialog rows + 1 distilled fact (#696 shape).

    Every row shares the single query token "kuma" (so FTS returns the
    dialog flood and ranks the fact beyond its top-k), and every dialog
    embedding is closer to the query than the fact's (so the dense pool
    would be saturated by dialog alone). The fact can only reach recall
    through the vector voice once dialog is excluded from the pool.
    """
    now = datetime.now().isoformat()
    query = _unit_query_vec()
    rows = []
    embeddings = []
    for i in range(dialog_count):
        mem_id = f"dialog-{i:04d}"
        rows.append(
            (
                mem_id,
                f"обсуждение прогноз погоды kuma день {i}",
                "conversation",
                now,
                "flood-session",
                "session",
            )
        )
        embeddings.append((mem_id, _near_vec(query, seed=i)))
    rows.append(
        (
            FACT_ID,
            "Создание мониторов Uptime Kuma только через CLI скрипт",
            "fact",
            now,
            "flood-session",
            "session",
        )
    )
    embeddings.append((FACT_ID, _far_vec(query)))

    conn = beam.conn
    conn.executemany(
        "INSERT INTO working_memory "
        "(id, content, source, timestamp, session_id, scope) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    for mem_id, vec in embeddings:
        conn.execute(
            "INSERT INTO memory_embeddings (memory_id, embedding_json, model) "
            "VALUES (?, ?, ?)",
            (mem_id, json_dumps(vec), "test-model"),
        )
    conn.commit()


def json_dumps(vec):
    import json

    return json.dumps(vec.astype(np.float32).tolist())


def _enable_embeddings(monkeypatch, query_vec):
    monkeypatch.setattr(beam_module._embeddings, "available", lambda: True)
    monkeypatch.setattr(
        beam_module._embeddings, "embed_query", lambda _query: query_vec
    )


def test_dialog_flood_does_not_starve_fact_from_vec_pool(temp_db, monkeypatch):
    """#696 regression (default lexical gate): a fact ranking inside the
    dialog flood still reaches recall once raw dialog is excluded from the
    dense pool. Pre-fix this fact is absent: FTS returns only dialog rows
    (it ranks beyond the top-k) and the vec pool is saturated by dialog."""
    beam = BeamMemory(session_id="flood-session", db_path=temp_db)
    _flood_db(beam)
    _enable_embeddings(monkeypatch, _unit_query_vec())

    results = beam.recall(QUERY, top_k=10)

    ids = [r["id"] for r in results]
    assert FACT_ID in ids, (
        "distilled fact starved out of the vec pool by dialog rows; "
        f"recall returned {ids[:5]}"
    )


def test_dialog_flood_pure_vector_case_recall_first_mode(temp_db, monkeypatch):
    """The pool exclusion also serves MNEMOSYNE_LEXICAL_GATE_MIN=0 (recall-
    first mode): with the gate open, a lexically invisible fact that only has
    a dense voice is admitted once dialog no longer occupies the pool."""
    monkeypatch.setenv("MNEMOSYNE_LEXICAL_GATE_MIN", "0.0")
    beam = BeamMemory(session_id="flood-session", db_path=temp_db)
    _flood_db(beam)
    # Override the fact's content: no shared token with the query at all.
    beam.conn.execute(
        "UPDATE working_memory SET content = ? WHERE id = ?",
        ("Создание мониторов Uptime Kuma только через CLI скрипт", FACT_ID),
    )
    beam.conn.commit()
    _enable_embeddings(monkeypatch, _unit_query_vec())

    results = beam.recall("nova alpha monitor add beta", top_k=10)
    ids = [r["id"] for r in results]
    assert FACT_ID in ids, "fact must reach recall via the dense voice alone"
    assert not any(i.startswith("dialog-") for i in ids), (
        "dialog rows must stay out of results when they match neither FTS "
        "nor (post-fix) the dense pool"
    )


def test_wm_vec_search_excludes_dialog_sources(temp_db):
    """The vec-search WHERE clause used by recall drops conversation and
    honcho rows while keeping NULL-source and distilled rows."""
    beam = BeamMemory(session_id="unit-session", db_path=temp_db)
    now = datetime.now().isoformat()
    conn = beam.conn
    conn.executemany(
        "INSERT INTO working_memory "
        "(id, content, source, timestamp, session_id, scope) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("fact-1", "fact content", "fact", now, "unit-session", "session"),
            ("conv-1", "conversation content", "conversation", now, "unit-session", "session"),
            ("honcho-1", "honcho import content", "honcho_message", now, "unit-session", "session"),
            ("null-src", "no source content", None, now, "unit-session", "session"),
        ],
    )
    conn.executemany(
        "INSERT INTO memory_embeddings (memory_id, embedding_json, model) VALUES (?, ?, ?)",
        [
            ("fact-1", "[1.0, 0.0, 0.0]", "test"),
            ("conv-1", "[1.0, 0.0, 0.0]", "test"),
            ("honcho-1", "[1.0, 0.0, 0.0]", "test"),
            ("null-src", "[1.0, 0.0, 0.0]", "test"),
        ],
    )
    conn.commit()

    where = (
        "(valid_until IS NULL OR valid_until > ?) AND superseded_by IS NULL "
        "AND (source IS NULL OR "
        "(source <> 'conversation' AND source NOT LIKE 'honcho%'))"
    )
    results = _wm_vec_search(
        conn,
        np.array([1.0, 0.0, 0.0], dtype=np.float32),
        k=10,
        where_sql=where,
        where_params=(now,),
    )
    assert {r["id"] for r in results} == {"fact-1", "null-src"}


def test_explicit_conversation_source_filter_still_returns_dialog(temp_db, monkeypatch):
    """Callers asking for conversation rows explicitly keep full access:
    the exclusion only applies to unfiltered recalls."""
    beam = BeamMemory(session_id="flood-session", db_path=temp_db)
    _flood_db(beam, dialog_count=20)
    _enable_embeddings(monkeypatch, _unit_query_vec())

    results = beam.recall(QUERY, top_k=10, source="conversation")
    ids = [r["id"] for r in results]
    assert any(i.startswith("dialog-") for i in ids), (
        "explicit source='conversation' recall must still surface dialog rows"
    )


def test_explicit_topic_filter_bypasses_default_exclusion(temp_db, monkeypatch):
    """topic= is stored in the source column, so an explicit topic filter
    must also bypass the default dense-source exclusion."""
    beam = BeamMemory(session_id="flood-session", db_path=temp_db)
    _flood_db(beam, dialog_count=20)
    _enable_embeddings(monkeypatch, _unit_query_vec())

    results = beam.recall(QUERY, top_k=10, topic="conversation")
    ids = [r["id"] for r in results]
    assert any(i.startswith("dialog-") for i in ids), (
        "explicit topic='conversation' recall must still surface dialog rows"
    )


def test_fts_only_default_recall_still_returns_conversation_rows(temp_db):
    """The exclusion is dense-pool-only: a default recall whose query matches
    conversation rows lexically must still return them via FTS."""
    beam = BeamMemory(session_id="flood-session", db_path=temp_db)
    _flood_db(beam, dialog_count=20)
    # No embeddings enabled: the vector voice is unavailable, FTS alone
    # must surface the matching conversation rows.
    results = beam.recall("обсуждение прогноз погоды", top_k=10)
    ids = [r["id"] for r in results]
    assert any(i.startswith("dialog-") for i in ids), (
        "FTS-only default recall must still return matching conversation rows"
    )


def test_default_vec_predicate_excludes_consolidated_rows(temp_db, monkeypatch):
    """#427: consolidated rows must not compete with hot unconsolidated
    memories in the default dense pool (mirrors get_context)."""
    monkeypatch.setenv("MNEMOSYNE_LEXICAL_GATE_MIN", "0.0")
    beam = BeamMemory(session_id="flood-session", db_path=temp_db)
    now = datetime.now().isoformat()
    query = _unit_query_vec()
    conn = beam.conn
    # Consolidated row: topically CLOSER to the query than the fact.
    conn.execute(
        "INSERT INTO working_memory "
        "(id, content, source, timestamp, session_id, scope, consolidated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("old-consolidated", "старый консолидированный факт про погоду", "fact",
         now, "flood-session", "session", now),
    )
    # Hot unconsolidated fact: semantically relevant but farther in the pool.
    conn.execute(
        "INSERT INTO working_memory "
        "(id, content, source, timestamp, session_id, scope) VALUES (?, ?, ?, ?, ?, ?)",
        ("hot-fact", "создание мониторов через CLI скрипт", "fact",
         now, "flood-session", "session"),
    )
    conn.execute(
        "INSERT INTO memory_embeddings (memory_id, embedding_json, model) VALUES (?, ?, ?)",
        ("old-consolidated", json_dumps(_near_vec(query, seed=1)), "test-model"),
    )
    conn.execute(
        "INSERT INTO memory_embeddings (memory_id, embedding_json, model) VALUES (?, ?, ?)",
        ("hot-fact", json_dumps(_far_vec(query)), "test-model"),
    )
    conn.commit()
    _enable_embeddings(monkeypatch, query)

    results = beam.recall("nova alpha monitor add beta", top_k=10)
    ids = [r["id"] for r in results]
    assert "hot-fact" in ids, (
        "hot unconsolidated fact must reach recall via the dense voice"
    )
    assert "old-consolidated" not in ids, (
        "consolidated rows must not compete for dense candidates by default"
    )


def test_polyphonic_path_applies_default_dense_source_filter(temp_db, monkeypatch):
    """MNEMOSYNE_POLYPHONIC_RECALL=1 returns before the linear predicate runs;
    the engine's vector voice must apply the same default dense-source
    exclusion before its top-K selection so dialog cannot starve the fact."""
    monkeypatch.setenv("MNEMOSYNE_POLYPHONIC_RECALL", "1")
    monkeypatch.setattr(
        "mnemosyne.core.local_llm.llm_available", lambda: False
    )
    beam = BeamMemory(session_id="flood-session", db_path=temp_db)
    _flood_db(beam, dialog_count=60)
    query = _unit_query_vec()
    monkeypatch.setattr(beam_module._embeddings, "available", lambda: True)
    monkeypatch.setattr(
        beam_module._embeddings, "embed_query", lambda _query: query
    )
    monkeypatch.setattr(
        beam_module._embeddings, "embed", lambda queries: [query]
    )

    results = beam.recall(QUERY, top_k=10)
    ids = [r["id"] for r in results]
    assert FACT_ID in ids, (
        "polyphonic vector voice must exclude dialog rows before top-K "
        "selection; recall returned %s" % (ids[:5],)
    )
