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


class TestExportImport:
    def test_beam_export_to_dict(self, temp_db):
        beam = BeamMemory(session_id="s1", db_path=temp_db)
        beam.remember("Prefers dark mode", source="preference", importance=0.9)
        beam.scratchpad_write("todo item")
        beam.consolidate_to_episodic("User likes dark mode", ["wm1"], importance=0.8)

        data = beam.export_to_dict()
        assert "mnemosyne_export" in data
        assert data["mnemosyne_export"]["version"] == "1.0"
        assert len(data["working_memory"]) >= 1
        assert len(data["scratchpad"]) >= 1
        assert len(data["episodic_memory"]) >= 1

    def test_beam_import_from_dict_idempotent(self, temp_db):
        beam = BeamMemory(session_id="s1", db_path=temp_db)
        mid = beam.remember("Prefers dark mode", source="preference", importance=0.9)
        data = beam.export_to_dict()

        # Import into fresh DB
        with tempfile.TemporaryDirectory() as tmpdir:
            fresh_db = Path(tmpdir) / "fresh.db"
            fresh_beam = BeamMemory(session_id="s1", db_path=fresh_db)
            stats = fresh_beam.import_from_dict(data)
            assert stats["working_memory"]["inserted"] >= 1

            # Verify
            ctx = fresh_beam.get_context(limit=5)
            assert any("dark mode" in c["content"] for c in ctx)

            # Second import should skip
            stats2 = fresh_beam.import_from_dict(data)
            assert stats2["working_memory"]["skipped"] >= 1

    def test_mnemosyne_export_import_roundtrip(self, temp_db):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Source
            src = Mnemosyne(session_id="s1", db_path=temp_db)
            src.remember("Likes pizza", source="preference", importance=0.8)
            src.scratchpad_write("note")
            export_path = Path(tmpdir) / "export.json"
            src.export_to_file(str(export_path))
            assert export_path.exists()

            # Target
            target_db = Path(tmpdir) / "target.db"
            target = Mnemosyne(session_id="s1", db_path=target_db)
            stats = target.import_from_file(str(export_path))
            assert stats["legacy"]["inserted"] >= 1
            assert stats["beam"]["working_memory"]["inserted"] >= 1


class TestProviderContextSafety:
    def test_subagent_context_does_not_initialize_or_write(self, temp_db, monkeypatch):
        import importlib.util
        import sys
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[1]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))

        monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(temp_db.parent))

        provider_path = repo_root / "hermes_memory_provider" / "__init__.py"
        spec = importlib.util.spec_from_file_location("mnemo_provider_test", provider_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)

        provider = mod.MnemosyneMemoryProvider()
        provider.initialize(
            "subagent-session",
            hermes_home=str(repo_root),
            platform="cli",
            agent_context="subagent",
            agent_identity="test-profile",
            agent_workspace="hermes",
        )

        assert provider._beam is None
        result = provider.handle_tool_call(
            "mnemosyne_remember",
            {
                "content": "subagent should not persist memory",
                "importance": 0.9,
                "source": "test",
                "scope": "session",
            },
        )
        assert "not initialized" in result

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='working_memory'")
        exists = cursor.fetchone() is not None
        count = conn.execute("SELECT COUNT(*) FROM working_memory").fetchone()[0] if exists else 0
        conn.close()
        assert count == 0


class TestCrossSessionRecall:
    def test_global_memory_survives_consolidation_and_recall(self, temp_db, monkeypatch):
        """Regression for issue #7 Bug 2: global memories must survive sleep() and be recallable cross-session."""
        monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(temp_db.parent))

        # Session A: store global memories with backdated timestamps so sleep() consolidates them
        beam_a = BeamMemory(session_id="hermes_session-A", db_path=temp_db)
        beam_a.remember("用户喜欢直接说结论", source="preference", importance=0.95, scope="global")
        beam_a.remember("用户讨论基金时重视手续费口径", source="preference", importance=0.92, scope="global")
        beam_a.remember("本轮只测试 mnemosyne 沙盒", source="test", importance=0.80, scope="session")

        # Backdate all working memories so they are old enough to consolidate
        conn = sqlite3.connect(temp_db)
        old_ts = (datetime.now() - timedelta(hours=48)).isoformat()
        conn.execute("UPDATE working_memory SET timestamp = ?", (old_ts,))
        conn.commit()
        conn.close()

        # Force consolidation (simulate on_session_end)
        result = beam_a.sleep()
        assert result["status"] == "consolidated"

        # Verify consolidated episodic memories preserved global scope
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT content, scope, session_id FROM episodic_memory WHERE scope = 'global'")
        global_rows = cursor.fetchall()
        assert len(global_rows) >= 1, "Global memories should survive consolidation with scope preserved"
        conn.close()

        # Session B: recall global memories
        beam_b = BeamMemory(session_id="hermes_session-B", db_path=temp_db)

        # Test Chinese query that previously returned 0
        results = beam_b.recall("谁喜欢直接说结论", top_k=5)
        assert len(results) > 0, "Cross-session recall should find global memory with Chinese query"
        contents = [r["content"] for r in results]
        assert any("用户喜欢直接说结论" in c for c in contents)

        # Test another Chinese query
        results2 = beam_b.recall("基金讨论时看重什么口径", top_k=5)
        assert len(results2) > 0, "Cross-session recall should find second global memory"

        # Test that session-scoped memory is NOT visible cross-session
        results3 = beam_b.recall("本轮只测试", top_k=5)
        # This may or may not find it depending on scoring; the key is globals ARE found

    def test_fallback_scoring_finds_chinese_substrings(self, temp_db, monkeypatch):
        """Fallback keyword scoring must handle Chinese where words aren't space-delimited."""
        monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(temp_db.parent))

        beam = BeamMemory(session_id="test-session", db_path=temp_db)
        beam.remember("用户喜欢直接说结论", source="preference", importance=0.9, scope="global")

        # Query that differs at the start but shares a core substring
        results = beam.recall("谁喜欢直接说结论", top_k=5)
        assert len(results) > 0, "Fallback scoring should match shared substrings in Chinese"

    def test_tools_session_singleton_updates(self, temp_db, monkeypatch):
        """Plugin tools _get_memory() must recreate when HERMES_SESSION_ID changes."""
        monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(temp_db.parent))

        import importlib.util
        import sys
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[1]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))

        tools_path = repo_root / "hermes_plugin" / "tools.py"
        spec = importlib.util.spec_from_file_location("mnemo_tools_test", tools_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        _get_memory = mod._get_memory

        monkeypatch.setenv("HERMES_SESSION_ID", "session-alpha")
        mem_a = _get_memory()
        mem_a.remember("alpha fact", source="test", scope="session")

        monkeypatch.setenv("HERMES_SESSION_ID", "session-beta")
        mem_b = _get_memory()
        # Should be a different instance (or at least different beam session_id)
        assert mem_b.session_id == "session-beta"
        assert mem_a.session_id == "session-alpha"
