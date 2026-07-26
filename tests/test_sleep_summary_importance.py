"""Sleep summaries inherit the batch's peak source importance.

Pre-fix, sleep() wrote every episodic summary at a flat importance=0.6,
discarding the signal callers set on the source rows. On real banks the
0.6 summaries then dominate recall's top slots on relevance while the
high-importance originals they summarize rank below them, inverting the
intent of importance-weighted recall.

Contract under test:

  summary importance = min(max(0.6, peak of source importances), 0.85)

  - a batch containing a 0.9 row yields a 0.85 summary (cap: a summary
    never outranks the strongest originals' band)
  - an all-default batch (0.5 rows) keeps the historical 0.6 exactly
  - a mid-importance peak (e.g. 0.75) passes through unchanged
"""

import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mnemosyne.core.beam import BeamMemory


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test.db"


def _seed_old_wm(db_path, session_id, importances, ts_offset_hours=200):
    """Insert one old working_memory row per importance value."""
    conn = sqlite3.connect(str(db_path))
    ts = (datetime.now() - timedelta(hours=ts_offset_hours)).isoformat()
    rows = [
        (f"imp-{session_id}-{i}", f"imp-content-{i}", "conversation", ts,
         session_id, imp)
        for i, imp in enumerate(importances)
    ]
    conn.executemany(
        "INSERT INTO working_memory "
        "(id, content, source, timestamp, session_id, importance) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _summary_importances(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return [r[0] for r in conn.execute(
            "SELECT importance FROM episodic_memory "
            "WHERE source = 'sleep_consolidation'"
        ).fetchall()]
    finally:
        conn.close()


class TestSleepSummaryImportance:

    def test_summary_inherits_peak_importance_capped(self, temp_db):
        """A 0.9 source row lifts the summary to the 0.85 cap."""
        beam = BeamMemory(session_id="s1", db_path=temp_db)
        _seed_old_wm(temp_db, "s1", [0.5, 0.9, 0.5])

        result = beam.sleep(dry_run=False)
        assert result["status"] == "consolidated"

        importances = _summary_importances(temp_db)
        assert len(importances) == 1
        assert importances[0] == pytest.approx(0.85)

    def test_all_default_batch_keeps_flat_floor(self, temp_db):
        """A batch of 0.5 autosave-style rows keeps the historical 0.6."""
        beam = BeamMemory(session_id="s1", db_path=temp_db)
        _seed_old_wm(temp_db, "s1", [0.5, 0.5, 0.5])

        result = beam.sleep(dry_run=False)
        assert result["status"] == "consolidated"

        importances = _summary_importances(temp_db)
        assert len(importances) == 1
        assert importances[0] == pytest.approx(0.6)

    def test_mid_importance_peak_passes_through(self, temp_db):
        """A peak inside (0.6, 0.85) is inherited unchanged."""
        beam = BeamMemory(session_id="s1", db_path=temp_db)
        _seed_old_wm(temp_db, "s1", [0.5, 0.75])

        result = beam.sleep(dry_run=False)
        assert result["status"] == "consolidated"

        importances = _summary_importances(temp_db)
        assert len(importances) == 1
        assert importances[0] == pytest.approx(0.75)
