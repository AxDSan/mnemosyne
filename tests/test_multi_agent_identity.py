"""
Tests for Multi-Agent Identity Layer (v2.1)

Tests cover:
- Schema migration adds identity columns
- remember() stores identity fields
- recall() filters by author_id, author_type, channel_id
- Cross-session channel recall
- get_stats() with identity filters
- MCP per-connection instances
- MCP env var fallback
- Backward compatibility (no identity = unchanged behavior)
"""

import pytest
import os
import tempfile
from pathlib import Path
from mnemosyne.core.beam import BeamMemory, init_beam
from mnemosyne.core.memory import Mnemosyne
from mnemosyne.mcp_tools import _create_instance


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    tmpdir = Path(tempfile.mkdtemp())
    db_path = tmpdir / "test.db"
    # Override data dir to isolate tests
    old_data_dir = os.environ.get("MNEMOSYNE_DATA_DIR")
    os.environ["MNEMOSYNE_DATA_DIR"] = str(tmpdir)
    yield db_path
    if old_data_dir:
        os.environ["MNEMOSYNE_DATA_DIR"] = old_data_dir
    else:
        del os.environ["MNEMOSYNE_DATA_DIR"]


class TestSchemaMigration:
    """Schema migration adds identity columns."""

    def test_working_memory_has_identity_columns(self, temp_db):
        bm = BeamMemory(session_id="test", db_path=temp_db)
        cols = [c[1] for c in bm.conn.execute("PRAGMA table_info(working_memory)").fetchall()]
        assert "author_id" in cols
        assert "author_type" in cols
        assert "channel_id" in cols

    def test_episodic_memory_has_identity_columns(self, temp_db):
        bm = BeamMemory(session_id="test", db_path=temp_db)
        cols = [c[1] for c in bm.conn.execute("PRAGMA table_info(episodic_memory)").fetchall()]
        assert "author_id" in cols
        assert "author_type" in cols
        assert "channel_id" in cols

    def test_identity_indexes_exist(self, temp_db):
        bm = BeamMemory(session_id="test", db_path=temp_db)
        idxs = [r[1] for r in bm.conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
        assert "idx_wm_author" in idxs
        assert "idx_wm_channel" in idxs
        assert "idx_em_author" in idxs
        assert "idx_em_channel" in idxs


class TestRememberIdentity:
    """remember() auto-populates identity fields."""

    def test_remember_stores_author(self, temp_db):
        mem = Mnemosyne(session_id="test", author_id="abdias", author_type="human",
                        channel_id="fluxspeak-team", db_path=temp_db)
        mid = mem.remember("Dark mode preference", importance=0.9)
        row = mem.conn.execute(
            "SELECT author_id, author_type, channel_id FROM working_memory WHERE id = ?",
            (mid,)
        ).fetchone()
        assert row["author_id"] == "abdias"
        assert row["author_type"] == "human"
        assert row["channel_id"] == "fluxspeak-team"

    def test_remember_without_identity_is_null(self, temp_db):
        mem = Mnemosyne(session_id="test", db_path=temp_db)
        mid = mem.remember("Some memory")
        row = mem.conn.execute(
            "SELECT author_id, author_type, channel_id FROM working_memory WHERE id = ?",
            (mid,)
        ).fetchone()
        assert row["author_id"] is None
        assert row["author_type"] is None
        # channel_id defaults to session_id
        assert row["channel_id"] == "test"

    def test_channel_id_defaults_to_session(self, temp_db):
        mem = Mnemosyne(session_id="my-channel", db_path=temp_db)
        mid = mem.remember("Channel-scoped memory")
        row = mem.conn.execute(
            "SELECT channel_id FROM working_memory WHERE id = ?", (mid,)
        ).fetchone()
        assert row["channel_id"] == "my-channel"

    def test_different_authors_same_channel(self, temp_db):
        abdias = Mnemosyne(session_id="a1", author_id="abdias", author_type="human",
                            channel_id="team", db_path=temp_db)
        sarah = Mnemosyne(session_id="a2", author_id="sarah", author_type="human",
                           channel_id="team", db_path=temp_db)
        abdias.remember("Dark mode")
        sarah.remember("Launch Friday")

        rows = abdias.conn.execute("SELECT author_id FROM working_memory ORDER BY timestamp").fetchall()
        authors = [r["author_id"] for r in rows]
        assert "abdias" in authors
        assert "sarah" in authors


class TestRecallIdentity:
    """recall() filters by identity."""

    def test_recall_by_author(self, temp_db):
        mem = Mnemosyne(session_id="test", author_id="abdias", db_path=temp_db)
        mem.remember("Dark mode preference", importance=0.9)

        results = mem.recall("dark", author_id="abdias")
        assert len(results) >= 1
        assert results[0]["author_id"] == "abdias"

    def test_recall_by_author_no_match(self, temp_db):
        mem = Mnemosyne(session_id="test", author_id="abdias", db_path=temp_db)
        mem.remember("Dark mode")

        results = mem.recall("dark", author_id="sarah")
        assert len(results) == 0

    def test_recall_by_author_type(self, temp_db):
        mem = Mnemosyne(session_id="test", author_id="bot-ci", author_type="agent",
                        db_path=temp_db)
        mem.remember("Deploy succeeded")
        mem2 = Mnemosyne(session_id="test2", author_id="abdias", author_type="human",
                         db_path=temp_db)
        mem2.remember("Dark mode")

        results = mem.recall("deploy", author_type="agent")
        assert len(results) >= 1
        results2 = mem.recall("dark", author_type="human", author_id="abdias")
        assert len(results2) >= 1

    def test_cross_session_channel_recall(self, temp_db):
        """Agents in different sessions but same channel can see each other's memories."""
        abdias = Mnemosyne(session_id="session-a", author_id="abdias",
                            channel_id="fluxspeak-team", db_path=temp_db)
        sarah = Mnemosyne(session_id="session-b", author_id="sarah",
                           channel_id="fluxspeak-team", db_path=temp_db)

        abdias.remember("Dark mode is preferred")
        sarah.remember("Launch is Friday")

        # Abdias recalls by channel — should see Sarah's memories too
        results = abdias.recall("Launch", channel_id="fluxspeak-team")
        assert len(results) >= 1
        assert results[0]["channel_id"] == "fluxspeak-team"

        # Sarah recalls by channel — should see Abdias's memories
        results = sarah.recall("dark", channel_id="fluxspeak-team")
        assert len(results) >= 1
        assert results[0]["channel_id"] == "fluxspeak-team"

    def test_channel_recall_excludes_other_channels(self, temp_db):
        a = Mnemosyne(session_id="a1", author_id="abdias", channel_id="team-a", db_path=temp_db)
        b = Mnemosyne(session_id="b1", author_id="sarah", channel_id="team-b", db_path=temp_db)

        a.remember("Team A secret")
        b.remember("Team B secret")

        results = a.recall("secret", channel_id="team-a")
        assert len(results) == 1
        assert results[0]["channel_id"] == "team-a"


class TestStatsIdentity:
    """get_stats() and get_working_stats() with identity filters."""

    def test_stats_by_author(self, temp_db):
        mem = Mnemosyne(session_id="test", author_id="abdias", db_path=temp_db)
        mem.remember("Memory 1")
        mem.remember("Memory 2")

        stats = mem.beam.get_working_stats(author_id="abdias")
        assert stats["total"] == 2

        stats_empty = mem.beam.get_working_stats(author_id="sarah")
        assert stats_empty["total"] == 0

    def test_stats_by_channel(self, temp_db):
        a = Mnemosyne(session_id="a1", author_id="abdias", channel_id="team", db_path=temp_db)
        b = Mnemosyne(session_id="b1", author_id="sarah", channel_id="team", db_path=temp_db)
        a.remember("A")
        b.remember("B")

        stats = a.beam.get_working_stats(channel_id="team")
        assert stats["total"] == 2


class TestMcpIdentity:
    """MCP per-connection instances and env var fallback."""

    def test_create_instance_with_args(self):
        mem = _create_instance(author_id="codex", author_type="agent",
                                channel_id="repo-dev", bank="default")
        assert mem.author_id == "codex"
        assert mem.author_type == "agent"
        assert mem.channel_id == "repo-dev"

    def test_create_instance_env_fallback(self):
        os.environ["MNEMOSYNE_AUTHOR_ID"] = "envuser"
        os.environ["MNEMOSYNE_AUTHOR_TYPE"] = "system"
        os.environ["MNEMOSYNE_CHANNEL_ID"] = "env-channel"
        try:
            mem = _create_instance()
            assert mem.author_id == "envuser"
            assert mem.author_type == "system"
            assert mem.channel_id == "env-channel"
        finally:
            del os.environ["MNEMOSYNE_AUTHOR_ID"]
            del os.environ["MNEMOSYNE_AUTHOR_TYPE"]
            del os.environ["MNEMOSYNE_CHANNEL_ID"]

    def test_create_instance_args_override_env(self):
        os.environ["MNEMOSYNE_AUTHOR_ID"] = "envuser"
        try:
            mem = _create_instance(author_id="override")
            assert mem.author_id == "override"
        finally:
            del os.environ["MNEMOSYNE_AUTHOR_ID"]


class TestBackwardCompatibility:
    """Without identity params, behavior is unchanged."""

    def test_recall_without_identity_filters(self, temp_db):
        mem = Mnemosyne(session_id="test", db_path=temp_db)
        mid = mem.remember("Dark mode")
        results = mem.recall("dark")
        assert len(results) >= 1
        assert results[0]["id"] == mid

    def test_old_constructor_still_works(self, temp_db):
        """The old Mnemosyne(session_id=..., db_path=...) still works."""
        mem = Mnemosyne(session_id="test", db_path=temp_db)
        mid = mem.remember("Works fine")
        assert mid is not None
        results = mem.recall("fine")
        assert len(results) == 1
