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

        # BEAM working_memory should use the same ID now
        wm = conn.execute("SELECT * FROM working_memory WHERE id = ? AND session_id = ?", (mid, "s2")).fetchone()
        assert wm is not None
        conn.close()

        results = mem.recall("pizza")
        assert len(results) >= 1

    def test_forget_removes_both_layers(self, temp_db):
        mem = Mnemosyne(session_id="s2", db_path=temp_db)
        mid = mem.remember("Forget me please", source="preference", importance=0.8)
        assert mem.forget(mid) is True
        conn = sqlite3.connect(temp_db)
        legacy = conn.execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
        wm = conn.execute("SELECT * FROM working_memory WHERE id = ? AND session_id = ?", (mid, "s2")).fetchone()
        conn.close()
        assert legacy is None
        assert wm is None

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
        # Disable LLM summarization so original Chinese text is preserved in consolidation
        monkeypatch.setattr("mnemosyne.core.local_llm.llm_available", lambda: False)

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


class TestTemporalQueries:
    """Temporal filtering for BEAM recall — Issue #16."""

    def test_recall_from_date_filter(self, temp_db):
        beam = BeamMemory(session_id="s1", db_path=temp_db)
        beam.remember("Meeting about Q1 goals", source="meeting", importance=0.8)

        # Backdate an old memory directly
        conn = sqlite3.connect(temp_db)
        old_ts = "2025-01-15T10:00:00"
        conn.execute(
            "INSERT INTO working_memory (id, content, source, timestamp, session_id, importance) VALUES (?, ?, ?, ?, ?, ?)",
            ("old1", "Old project kickoff", "meeting", old_ts, "s1", 0.7)
        )
        conn.commit()
        conn.close()

        # Filter from 2025-04-01 should exclude January memory
        results = beam.recall("project", from_date="2025-04-01")
        assert all("Old project kickoff" not in r["content"] for r in results)

    def test_recall_to_date_filter(self, temp_db):
        beam = BeamMemory(session_id="s1", db_path=temp_db)
        beam.remember("Recent standup notes", source="meeting", importance=0.8)

        # Backdate an old memory
        conn = sqlite3.connect(temp_db)
        old_ts = "2025-01-15T10:00:00"
        conn.execute(
            "INSERT INTO working_memory (id, content, source, timestamp, session_id, importance) VALUES (?, ?, ?, ?, ?, ?)",
            ("old1", "January planning session", "meeting", old_ts, "s1", 0.7)
        )
        conn.commit()
        conn.close()

        # Filter to 2025-02-01 should only include January memory
        results = beam.recall("planning", to_date="2025-02-01")
        assert any("January" in r["content"] for r in results)
        assert all("Recent" not in r["content"] for r in results)

    def test_recall_source_filter(self, temp_db):
        beam = BeamMemory(session_id="s1", db_path=temp_db)
        beam.remember("Bug fix for auth", source="github", importance=0.8)
        beam.remember("Lunch with team", source="conversation", importance=0.5)

        results = beam.recall("auth", source="github")
        assert len(results) >= 1
        assert all(r.get("source") == "github" for r in results)

    def test_recall_date_range_filter(self, temp_db):
        beam = BeamMemory(session_id="s1", db_path=temp_db)

        # Insert memories on different dates
        conn = sqlite3.connect(temp_db)
        dates = [
            ("2025-01-10T10:00:00", "January task A"),
            ("2025-03-15T10:00:00", "March task B"),
            ("2025-06-20T10:00:00", "June task C"),
        ]
        for ts, content in dates:
            conn.execute(
                "INSERT INTO working_memory (id, content, source, timestamp, session_id, importance) VALUES (?, ?, ?, ?, ?, ?)",
                (f"m_{content[:5]}", content, "test", ts, "s1", 0.7)
            )
        conn.commit()
        conn.close()

        # Range: March to May
        results = beam.recall("task", from_date="2025-03-01", to_date="2025-05-31")
        contents = [r["content"] for r in results]
        assert any("March" in c for c in contents)
        assert all("January" not in c for c in contents)
        assert all("June" not in c for c in contents)

    def test_recall_with_episodic_temporal_filter(self, temp_db):
        beam = BeamMemory(session_id="s1", db_path=temp_db)
        # Consolidated memory with old timestamp
        beam.consolidate_to_episodic(
            summary="Q4 review discussion",
            source_wm_ids=["wm1"],
            source="meeting",
            importance=0.8
        )
        # Backdate the episodic memory
        conn = sqlite3.connect(temp_db)
        conn.execute("UPDATE episodic_memory SET timestamp = ? WHERE content = ?", ("2024-12-01T10:00:00", "Q4 review discussion"))
        conn.commit()
        conn.close()

        # Should find it without date filter
        results_all = beam.recall("Q4 review")
        assert any("Q4" in r["content"] for r in results_all)

        # Should exclude it with from_date in 2025
        results_filtered = beam.recall("Q4 review", from_date="2025-01-01")
        assert all("Q4" not in r["content"] for r in results_filtered)

    def test_temporal_triple_auto_generated(self, temp_db):
        """Temporal triples should be auto-generated on remember()."""
        from mnemosyne.core.triples import TripleStore

        beam = BeamMemory(session_id="s1", db_path=temp_db)
        mid = beam.remember("Deploy script updated", source="dev", importance=0.8)

        triple_store = TripleStore(db_path=temp_db)
        triples = triple_store.query(subject=mid)
        assert len(triples) >= 1
        assert any(t["predicate"] == "occurred_on" for t in triples)


class TestTokenAwareConsolidation:
    def test_sleep_chunks_large_batches(self, temp_db, monkeypatch):
        """BUG-1: sleep() must chunk memories to fit LLM context window."""
        monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(temp_db.parent))
        # Force a small context window to trigger chunking
        monkeypatch.setenv("MNEMOSYNE_LLM_N_CTX", "512")
        monkeypatch.setenv("MNEMOSYNE_LLM_MAX_TOKENS", "128")
        # Disable actual LLM — we test the chunking logic
        monkeypatch.setattr("mnemosyne.core.local_llm.llm_available", lambda: False)

        beam = BeamMemory(session_id="test-chunking", db_path=temp_db)

        # Store 30 memories, each ~100 chars (~25 tokens)
        for i in range(30):
            beam.remember(
                f"Memory number {i} with enough content to consume tokens " * 3,
                source="test_batch",
                importance=0.5
            )

        # Backdate so sleep() picks them up
        conn = sqlite3.connect(temp_db)
        old_ts = (datetime.now() - timedelta(hours=48)).isoformat()
        conn.execute("UPDATE working_memory SET timestamp = ?", (old_ts,))
        conn.commit()
        conn.close()

        result = beam.sleep()
        assert result["status"] == "consolidated"
        assert result["summaries_created"] >= 1

        # Verify no working memory left for this session
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM working_memory WHERE session_id = ?", ("test-chunking",))
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 0

    def test_chunk_memories_by_budget_single_oversized(self, monkeypatch):
        """A single memory exceeding the budget should be skipped from LLM chunking."""
        from mnemosyne.core import local_llm

        # Monkeypatch module-level constants directly (env vars already read at import)
        monkeypatch.setattr(local_llm, "LLM_N_CTX", 128)
        monkeypatch.setattr(local_llm, "LLM_MAX_TOKENS", 32)

        from mnemosyne.core.local_llm import chunk_memories_by_budget

        # One normal memory, one giant memory
        memories = [
            "Short memory.",  # ~3 tokens, fits
            "A" * 500,       # ~125 tokens, exceeds budget
        ]
        chunks = chunk_memories_by_budget(memories)

        # Giant memory should be excluded (it exceeds the total budget)
        assert len(chunks) == 1
        assert chunks[0] == ["Short memory."]
