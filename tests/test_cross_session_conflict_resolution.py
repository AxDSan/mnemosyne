"""Tests for cross-session global contradiction resolution.

The pass resolves factual contradictions among `scope='global'` memories across
different sessions so recall/prefetch stop co-presenting the stale and current
versions. Conflict *detection* needs embeddings, which are unavailable in CI
(`MNEMOSYNE_NO_EMBEDDINGS=1`), so the tests stub `BeamMemory._detect_conflicts`
to return a deterministic cross-session pair and assert the resolution (cross-
session `invalidate` + recall filtering) is real and non-vacuous.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mnemosyne.core.beam import BeamMemory


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test.db"


@pytest.fixture
def disable_llm(monkeypatch):
    monkeypatch.setattr("mnemosyne.core.local_llm.llm_available", lambda: False)
    # pin the deterministic linear recall path (recall needs embeddings for the
    # polyphonic path; the linear path works under MNEMOSYNE_NO_EMBEDDINGS).
    monkeypatch.setenv("MNEMOSYNE_POLYPHONIC_RECALL", "0")


def _stub_detect_pair(*ids):
    return lambda items, similarity_threshold=0.88, min_gap_hours=1.0: [
        (ids[i], ids[i + 1]) for i in range(len(ids) - 1)
    ]


def test_global_contradiction_resolved_across_sessions(temp_db, monkeypatch, disable_llm):
    """Two contrary `global` facts in different sessions both surface from a
    third session; after resolution the older is superseded and recall returns
    only the newer."""
    monkeypatch.setenv("MNEMOSYNE_CROSS_SESSION_CONFLICT_RESOLUTION", "1")
    A = BeamMemory(session_id="tA", db_path=temp_db)
    B = BeamMemory(session_id="tB", db_path=temp_db)
    C = BeamMemory(session_id="tC", db_path=temp_db)

    blue = A.remember("[USER] favorite color is blue", source="conversation",
                      importance=0.7, scope="global")
    green = B.remember("[USER] favorite color is green now", source="conversation",
                       importance=0.7, scope="global")

    before = {r["id"] for r in C.recall("favorite color", top_k=10)}
    assert blue in before and green in before, "sanity: both global rows surface cross-session"

    C._detect_conflicts = _stub_detect_pair(blue, green)
    res = C.resolve_cross_session_conflicts()
    assert res["status"] == "resolved"
    assert res["invalidated"] == 1 and res["conflicts_resolved"] == 1

    after = {r["id"] for r in C.recall("favorite color", top_k=10)}
    assert blue not in after and green in after, "loser hidden, winner remains"

    row = C.conn.execute(
        "SELECT superseded_by FROM working_memory WHERE id = ?", (blue,)
    ).fetchone()
    assert row is not None and row[0] == green, "older row marked superseded by newer"


def test_disabled_by_default_noop(temp_db, disable_llm):
    """Without the feature flag the pass is a no-op (opt-in behavior)."""
    A = BeamMemory(session_id="tA", db_path=temp_db)
    b = A.remember("[USER] favorite color is blue", source="conversation",
                   importance=0.7, scope="global")
    A.remember("[USER] favorite color is green now", source="conversation",
               importance=0.7, scope="global")
    A._detect_conflicts = _stub_detect_pair(b, "replacement")
    res = A.resolve_cross_session_conflicts()
    assert res["status"] == "disabled"
    row = A.conn.execute(
        "SELECT superseded_by FROM working_memory WHERE id = ?", (b,)
    ).fetchone()
    assert row[0] is None


def test_session_private_rows_not_scanned(temp_db, monkeypatch, disable_llm):
    """Session-private (non-global) rows are never considered or mutated."""
    monkeypatch.setenv("MNEMOSYNE_CROSS_SESSION_CONFLICT_RESOLUTION", "1")
    A = BeamMemory(session_id="tA", db_path=temp_db)
    private = A.remember("[USER] a private thought", source="conversation",
                         importance=0.5, scope="session")
    A._detect_conflicts = lambda items, similarity_threshold=0.88, min_gap_hours=1.0: []
    res = A.resolve_cross_session_conflicts()
    assert res["rows_scanned"] == 0, "only global rows should be selected"
    row = A.conn.execute(
        "SELECT superseded_by FROM working_memory WHERE id = ?", (private,)
    ).fetchone()
    assert row[0] is None


def test_llm_branch_confirms_and_declines(temp_db, monkeypatch, disable_llm):
    """With LLM conflict detection on, a confirmed pair is resolved and a
    declined pair is left untouched (the LLM gate is the context/ambiguity
    backstop)."""
    monkeypatch.setenv("MNEMOSYNE_CROSS_SESSION_CONFLICT_RESOLUTION", "1")
    import mnemosyne.core.llm_conflict_detector as lcd
    monkeypatch.setattr(lcd, "LLM_CONFLICT_DETECTION_ENABLED", True)

    A = BeamMemory(session_id="tA", db_path=temp_db)
    B = BeamMemory(session_id="tB", db_path=temp_db)
    C = BeamMemory(session_id="tC", db_path=temp_db)

    # confirmed
    blue = A.remember("[USER] favorite color is blue", source="conversation",
                      importance=0.7, scope="global")
    green = B.remember("[USER] favorite color is green now", source="conversation",
                       importance=0.7, scope="global")
    C._detect_conflicts = _stub_detect_pair(blue, green)
    monkeypatch.setattr(lcd, "validate_conflict_pair",
                        lambda older, newer, session_id, db_path: (True, 0.9, "green"))
    assert C.resolve_cross_session_conflicts()["invalidated"] == 1

    # declined (different context / complement): nothing superseded
    b2 = A.remember("[USER] favorite color is mauve", source="conversation",
                    importance=0.7, scope="global")
    g2 = B.remember("[USER] favorite color is green now", source="conversation",
                    importance=0.7, scope="global")
    C2 = BeamMemory(session_id="tC2", db_path=temp_db)
    C2._detect_conflicts = _stub_detect_pair(b2, g2)
    monkeypatch.setattr(lcd, "validate_conflict_pair",
                        lambda older, newer, session_id, db_path: (False, 0.2, None))
    res = C2.resolve_cross_session_conflicts()
    assert res["invalidated"] == 0 and res["conflicts_resolved"] == 0


def test_min_gap_hours_parametrizes_detection(temp_db, monkeypatch, disable_llm):
    """The cross-session pass forwards a relaxed min_gap_hours to detection."""
    monkeypatch.setenv("MNEMOSYNE_CROSS_SESSION_CONFLICT_RESOLUTION", "1")
    A = BeamMemory(session_id="tA", db_path=temp_db)
    seen = {}
    A._detect_conflicts = lambda items, similarity_threshold=0.88, min_gap_hours=1.0: (
        seen.update(min_gap_hours=min_gap_hours, items=len(items)) or []
    )
    A.remember("[USER] favorite color is blue", source="conversation",
               importance=0.7, scope="global")
    A.remember("[USER] favorite color is green now", source="conversation",
               importance=0.7, scope="global")
    A.resolve_cross_session_conflicts()
    assert seen.get("min_gap_hours") == 0.0, "cross-session default gap is 0"


def test_sleep_retirement_guard_excludes_superseded(temp_db, monkeypatch, disable_llm):
    """A superseded-but-unconsolidated working row is not folded into a fresh
    episodic summary (retirement guard)."""
    A = BeamMemory(session_id="tA", db_path=temp_db)
    normal = A.remember("[USER] alpha normal memory", source="conversation", importance=0.6)
    stale = A.remember("[USER] beta stale memory", source="conversation", importance=0.6)
    replacement = A.remember("[USER] gamma correction memory", source="conversation", importance=0.6)
    A.invalidate(stale, replacement_id=replacement)  # sets superseded_by

    A.sleep(force=True)

    c_normal = A.conn.execute(
        "SELECT consolidated_at FROM working_memory WHERE id = ?", (normal,)
    ).fetchone()
    c_stale = A.conn.execute(
        "SELECT consolidated_at FROM working_memory WHERE id = ?", (stale,)
    ).fetchone()
    assert c_normal[0] is not None, "normal row should be consolidated"
    assert c_stale[0] is None, "superseded row must not be consolidated"
