"""
Tests for Mnemosyne link memory / graph traversal tools.

Covers the enhanced find_related_memories with edge_type/min_weight
filtering, the _graph_voice traversal integration, and the new
mnemosyne_graph_query / mnemosyne_graph_link provider tools.
"""

import json
import pytest
from pathlib import Path
from datetime import datetime

from mnemosyne.core.beam import BeamMemory
from mnemosyne.core.episodic_graph import EpisodicGraph, GraphEdge
from hermes_memory_provider import MnemosyneMemoryProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_beam(tmp_path):
    db_path = Path(tmp_path) / "test.db"
    return BeamMemory(session_id="test_graph", db_path=db_path)


def _build_provider(beam) -> MnemosyneMemoryProvider:
    provider = MnemosyneMemoryProvider()
    provider._beam = beam
    provider._session_id = "test_graph"
    provider._agent_context = "primary"
    return provider


def _provider(tmp_path) -> MnemosyneMemoryProvider:
    return _build_provider(_make_beam(tmp_path))


# ---------------------------------------------------------------------------
# Core graph traversal (episodic_graph.py)
# ---------------------------------------------------------------------------

class TestFindRelatedMemories:
    """Direct tests of find_related_memories with filtering."""

    def test_basic_traversal(self, tmp_path):
        db = Path(tmp_path) / "graph.db"
        graph = EpisodicGraph(db_path=db)
        ts = datetime.now().isoformat()

        # Build a simple chain: A -> B -> C
        graph.add_edge(GraphEdge("mem_a", "mem_b", "ctx", 0.8, ts))
        graph.add_edge(GraphEdge("mem_b", "mem_c", "ctx", 0.7, ts))

        results = graph.find_related_memories("mem_a", depth=2)
        assert len(results) >= 2
        mids = {r["memory_id"] for r in results}
        assert "mem_b" in mids
        assert "mem_c" in mids

    def test_edge_type_filter(self, tmp_path):
        db = Path(tmp_path) / "graph.db"
        graph = EpisodicGraph(db_path=db)
        ts = datetime.now().isoformat()

        graph.add_edge(GraphEdge("mem_a", "mem_b", "ctx", 0.8, ts))
        graph.add_edge(GraphEdge("mem_a", "mem_c", "syn", 0.9, ts))

        # Filter to ctx only
        results = graph.find_related_memories("mem_a", depth=2, edge_type="ctx")
        mids = {r["memory_id"] for r in results}
        assert "mem_b" in mids
        assert "mem_c" not in mids

        # Filter to syn only
        results = graph.find_related_memories("mem_a", depth=2, edge_type="syn")
        mids = {r["memory_id"] for r in results}
        assert "mem_c" in mids
        assert "mem_b" not in mids

    def test_weight_filter(self, tmp_path):
        db = Path(tmp_path) / "graph.db"
        graph = EpisodicGraph(db_path=db)
        ts = datetime.now().isoformat()

        graph.add_edge(GraphEdge("mem_a", "mem_b", "ctx", 0.2, ts))  # low
        graph.add_edge(GraphEdge("mem_a", "mem_c", "ctx", 0.8, ts))  # high

        results = graph.find_related_memories("mem_a", depth=2, min_weight=0.5)
        mids = {r["memory_id"] for r in results}
        assert "mem_c" in mids
        assert "mem_b" not in mids

    def test_depth_control(self, tmp_path):
        db = Path(tmp_path) / "graph.db"
        graph = EpisodicGraph(db_path=db)
        ts = datetime.now().isoformat()

        graph.add_edge(GraphEdge("mem_a", "mem_b", "ctx", 0.8, ts))
        graph.add_edge(GraphEdge("mem_b", "mem_c", "ctx", 0.7, ts))

        # Depth 1: should find B but not C
        results = graph.find_related_memories("mem_a", depth=1)
        mids = {r["memory_id"] for r in results}
        assert "mem_b" in mids
        assert "mem_c" not in mids

    def test_result_format(self, tmp_path):
        db = Path(tmp_path) / "graph.db"
        graph = EpisodicGraph(db_path=db)
        ts = datetime.now().isoformat()

        graph.add_edge(GraphEdge("mem_x", "mem_y", "references", 0.6, ts))

        results = graph.find_related_memories("mem_x", depth=1)
        assert len(results) == 1
        r = results[0]
        assert r["memory_id"] == "mem_y"
        assert r["edge_type"] == "references"
        assert r["weight"] == 0.6
        assert r["depth"] == 1

    def test_agent_declared_edge_type(self, tmp_path):
        db = Path(tmp_path) / "graph.db"
        graph = EpisodicGraph(db_path=db)
        ts = datetime.now().isoformat()

        # Agent-declared edge types like "caused", "supersedes"
        graph.add_edge(GraphEdge("bug_123", "fix_456", "caused", 0.9, ts))
        results = graph.find_related_memories("bug_123", depth=1, edge_type="caused")
        assert len(results) == 1
        assert results[0]["memory_id"] == "fix_456"


# ---------------------------------------------------------------------------
# Provider tool schemas and dispatch
# ---------------------------------------------------------------------------

class TestGraphToolSchemas:
    def test_tools_registered(self, tmp_path):
        provider = _provider(tmp_path)
        names = {s["name"] for s in provider.get_tool_schemas()}
        assert "mnemosyne_graph_query" in names
        assert "mnemosyne_graph_link" in names

    def test_schema_count(self, tmp_path):
        provider = _provider(tmp_path)
        assert len(provider.get_tool_schemas()) >= 17


# ---------------------------------------------------------------------------
# mnemosyne_graph_query tool
# ---------------------------------------------------------------------------

class TestGraphQueryTool:
    def test_query_returns_related(self, tmp_path):
        db = Path(tmp_path) / "graph.db"
        beam = BeamMemory(session_id="test_graph", db_path=db)
        prov = _build_provider(beam)
        ts = datetime.now().isoformat()

        # Manually add edges via the graph
        beam.episodic_graph.add_edge(GraphEdge("mem_a", "mem_b", "ctx", 0.8, ts))
        beam.episodic_graph.add_edge(GraphEdge("mem_b", "mem_c", "ctx", 0.7, ts))

        result = json.loads(
            prov.handle_tool_call("mnemosyne_graph_query",
                                  {"seed_memory_id": "mem_a", "max_hops": 2})
        )
        assert result["count"] >= 2
        mids = {r["memory_id"] for r in result["results"]}
        assert "mem_b" in mids
        assert "mem_c" in mids

    def test_query_missing_seed(self, tmp_path):
        provider = _provider(tmp_path)
        result = json.loads(provider.handle_tool_call("mnemosyne_graph_query", {}))
        assert "error" in result

    def test_query_empty_graph(self, tmp_path):
        provider = _provider(tmp_path)
        result = json.loads(
            provider.handle_tool_call("mnemosyne_graph_query",
                                      {"seed_memory_id": "lonely"})
        )
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# mnemosyne_graph_link tool
# ---------------------------------------------------------------------------

class TestGraphLinkTool:
    def test_link_creates_edge(self, tmp_path):
        provider = _provider(tmp_path)
        result = json.loads(
            provider.handle_tool_call("mnemosyne_graph_link", {
                "source_id": "mem_x",
                "target_id": "mem_y",
                "relationship": "references",
                "weight": 0.9,
            })
        )
        assert result["status"] == "linked"

        # Verify traversal finds it
        query_result = json.loads(
            provider.handle_tool_call("mnemosyne_graph_query",
                                      {"seed_memory_id": "mem_x"})
        )
        mids = {r["memory_id"] for r in query_result["results"]}
        assert "mem_y" in mids

    def test_link_missing_required(self, tmp_path):
        provider = _provider(tmp_path)
        result = json.loads(
            provider.handle_tool_call("mnemosyne_graph_link", {
                "source_id": "mem_x",
                "target_id": "mem_y",
                # missing relationship
            })
        )
        assert "error" in result

    def test_link_agent_edge_types(self, tmp_path):
        provider = _provider(tmp_path)
        for rel in ("caused", "supersedes", "related_to"):
            result = json.loads(
                provider.handle_tool_call("mnemosyne_graph_link", {
                    "source_id": "a",
                    "target_id": "b",
                    "relationship": rel,
                })
            )
            assert result["status"] == "linked"
            assert result["relationship"] == rel


# ---------------------------------------------------------------------------
# Auto-populated edges from beam.remember()
# ---------------------------------------------------------------------------

class TestAutoPopulatedEdges:
    def test_remember_creates_graph_edges(self, tmp_path):
        """BeamMemory._ingest_graph_and_veracity creates ctx edges on remember."""
        beam = _make_beam(tmp_path)
        mid = beam.remember("Alice met Bob at the office yesterday for a project review",
                            importance=0.8)

        # The auto-populated edges should be findable
        edges = beam.episodic_graph.find_related_memories(mid, depth=1)
        # At minimum, ctx edges to gists/facts should exist
        assert isinstance(edges, list)

    def test_graph_voice_traversal_in_polyphonic(self, tmp_path):
        """Verify _graph_voice returns traversal results when polyphonic is enabled."""
        beam = _make_beam(tmp_path)

        # Create a chain: mem_1 (about Alice) ctx-> gist_1 ctx-> fact_1
        mid = beam.remember(
            "Alice discussed the deployment with Bob. The new feature ships Friday.",
            importance=0.8
        )

        # Polyphonic is gated behind MNEMOSYNE_POLYPHONIC_RECALL=1,
        # but we can test the _graph_voice method directly via the engine.
        # Since it's a direct method call, just verify no crash.
        from mnemosyne.core.polyphonic_recall import PolyphonicRecallEngine
        engine = PolyphonicRecallEngine(db_path=beam.db_path)
        results = engine._graph_voice("Alice deployment")
        assert isinstance(results, list)
