"""
Tests for Mnemosyne BEAM architecture
"""

import pytest
import tempfile
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

from mnemosyne.core.beam import BeamMemory, init_beam
from mnemosyne.core.memory import Mnemosyne


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield db_path


class TestBeamSchema:
    def test_init_creates_tables(self, temp_db):
        init_beam(temp_db)
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        tables = [r[0] for r in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert "working_memory" in tables
        assert "episodic_memory" in tables
        assert "scratchpad" in tables
        assert "consolidation_log" in tables
        # FTS5 virtual table
        assert "fts_episodes" in tables
        conn.close()


class TestWorkingMemory:
    def test_remember_and_context(self, temp_db):
        beam = BeamMemory(session_id="s1", db_path=temp_db)
        mid = beam.remember("Prefers Neovim", source="preference", importance=0.9)
        assert mid is not None

        ctx = beam.get_context(limit=5)
        assert len(ctx) == 1
        assert ctx[0]["content"] == "Prefers Neovim"

    def test_trim_old_memories(self, temp_db):
        beam = BeamMemory(session_id="s1", db_path=temp_db)
        # Insert old memory directly
        conn = sqlite3.connect(temp_db)
        old_ts = (datetime.now() - timedelta(hours=25)).isoformat()
        conn.execute(
            "INSERT INTO working_memory (id, content, source, timestamp, session_id) VALUES (?, ?, ?, ?, ?)",
            ("old1", "old content", "conversation", old_ts, "s1")
        )
        conn.commit()
        conn.close()

        beam._trim_working_memory()
        stats = beam.get_working_stats()
        assert stats["total"] == 0


class TestEpisodicMemory:
    def test_consolidate_and_recall(self, temp_db):
        beam = BeamMemory(session_id="s1", db_path=temp_db)
        eid = beam.consolidate_to_episodic(
            summary="User likes dark mode",
            source_wm_ids=["wm1"],
            importance=0.8
        )
        assert eid is not None

        results = beam.recall("dark mode")
        assert len(results) >= 1
        assert any(r["tier"] == "episodic" for r in results)

    def test_recall_hybrid_ranking(self, temp_db):
        beam = BeamMemory(session_id="s1", db_path=temp_db)
        beam.consolidate_to_episodic("Python is the best language", ["a"], importance=0.7)
        beam.consolidate_to_episodic("Rust is great for systems", ["b"], importance=0.7)

        results = beam.recall("best programming language")
        assert len(results) >= 1


class TestScratchpad:
    def test_scratchpad_write_read_clear(self, temp_db):
        beam = BeamMemory(session_id="s1", db_path=temp_db)
        beam.scratchpad_write("todo: fix auth")
        entries = beam.scratchpad_read()
        assert len(entries) == 1
        assert "fix auth" in entries[0]["content"]

        beam.scratchpad_clear()
        assert len(beam.scratchpad_read()) == 0


class TestSleepCycle:
    def test_sleep_consolidates_old_memories(self, temp_db):
        beam = BeamMemory(session_id="s1", db_path=temp_db)
        # Inject old working memories
        conn = sqlite3.connect(temp_db)
        old_ts = (datetime.now() - timedelta(hours=20)).isoformat()
        for i in range(3):
            conn.execute(
                "INSERT INTO working_memory (id, content, source, timestamp, session_id) VALUES (?, ?, ?, ?, ?)",
                (f"old{i}", f"task {i}", "conversation", old_ts, "s1")
            )
        conn.commit()
        conn.close()

        result = beam.sleep(dry_run=False)
        assert result["status"] == "consolidated"
        assert result["items_consolidated"] == 3

        log = beam.get_consolidation_log(limit=1)
        assert len(log) == 1
        assert log[0]["items_consolidated"] == 3

    def test_sleep_dry_run(self, temp_db):
        beam = BeamMemory(session_id="s1", db_path=temp_db)
        conn = sqlite3.connect(temp_db)
        old_ts = (datetime.now() - timedelta(hours=20)).isoformat()
        conn.execute(
            "INSERT INTO working_memory (id, content, source, timestamp, session_id) VALUES (?, ?, ?, ?, ?)",
            ("old1", "task one", "conversation", old_ts, "s1")
        )
        conn.commit()
        conn.close()

        result = beam.sleep(dry_run=True)
        assert result["status"] == "dry_run"
        assert result["items_consolidated"] == 1
        # Should not actually delete
        stats = beam.get_working_stats()
        assert stats["total"] == 1


class TestMnemosyneIntegration:
    def test_legacy_and_beam_dual_write(self, temp_db):
        mem = Mnemosyne(session_id="s2", db_path=temp_db)
        mid = mem.remember("Likes pizza", source="preference", importance=0.8)

        # Legacy table
        conn = sqlite3.connect(temp_db)
        legacy = conn.execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
        assert legacy is not None

        # BEAM working_memory
        wm = conn.execute("SELECT * FROM working_memory WHERE session_id = ?", ("s2",)).fetchone()
        assert wm is not None
        conn.close()

        results = mem.recall("pizza")
        assert len(results) >= 1

    def test_beam_stats(self, temp_db):
        mem = Mnemosyne(session_id="s3", db_path=temp_db)
        mem.remember("Test stat", importance=0.5)
        stats = mem.get_stats()
        assert stats["mode"] == "beam"
        assert "beam" in stats
        assert "working_memory" in stats["beam"]
        assert "episodic_memory" in stats["beam"]
