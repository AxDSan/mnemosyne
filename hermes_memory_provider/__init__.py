"""Mnemosyne Memory Provider for Hermes.

Deploy to Hermes via:
    ln -s /path/to/mnemosyne/hermes_memory_provider ~/.hermes/plugins/mnemosyne

Then set in ~/.hermes/config.yaml:
    memory:
      provider: mnemosyne

This gives Mnemosyne first-class MemoryProvider integration (system prompt
injection, pre-turn prefetch, post-turn sync, tool dispatch) while remaining
a standalone plugin deployed through the plugin system.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure mnemosyne core is importable from this directory
_mnemosyne_root = Path(__file__).resolve().parent.parent
if str(_mnemosyne_root) not in sys.path:
    sys.path.insert(0, str(_mnemosyne_root))

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports — fail gracefully if mnemosyne core is missing
# ---------------------------------------------------------------------------

def _get_beam_class():
    from mnemosyne.core.beam import BeamMemory
    return BeamMemory


def _get_triple_module():
    from mnemosyne.core.triples import add_triple, query_triples
    return add_triple, query_triples


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

REMEMBER_SCHEMA = {
    "name": "mnemosyne_remember",
    "description": (
        "Store a durable memory in Mnemosyne. Use for ANY fact, preference, "
        "insight, or context that should persist across sessions. Higher importance "
        "(0.0-1.0) surfaces the memory more often. Use scope='global' for user-level "
        "facts; scope='session' for conversation-specific context. Use valid_until "
        "(ISO date YYYY-MM-DD) for time-bound facts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The memory content to store."},
            "importance": {"type": "number", "description": "Importance 0.0-1.0. Default 0.5.", "default": 0.5},
            "source": {"type": "string", "description": "Source tag: preference, fact, insight, task, etc.", "default": "user"},
            "scope": {"type": "string", "description": "'session' (default) or 'global'.", "default": "session"},
            "valid_until": {"type": "string", "description": "Optional expiry date YYYY-MM-DD.", "default": ""},
        },
        "required": ["content"],
    },
}

RECALL_SCHEMA = {
    "name": "mnemosyne_recall",
    "description": (
        "Search Mnemosyne for relevant memories. Uses hybrid ranking: 50% vector "
        "similarity + 30% FTS5 text rank + 20% importance. Returns ranked results."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural language query."},
            "limit": {"type": "integer", "description": "Max results. Default 5.", "default": 5},
        },
        "required": ["query"],
    },
}

SLEEP_SCHEMA = {
    "name": "mnemosyne_sleep",
    "description": (
        "Run the Mnemosyne consolidation cycle. Compresses old working memories "
        "into episodic summaries. Call after long sessions or when memory feels stale."
    ),
    "parameters": {"type": "object", "properties": {}},
}

STATS_SCHEMA = {
    "name": "mnemosyne_stats",
    "description": "Return Mnemosyne memory statistics: working count, episodic count, BEAM tiers.",
    "parameters": {"type": "object", "properties": {}},
}

INVALIDATE_SCHEMA = {
    "name": "mnemosyne_invalidate",
    "description": (
        "Mark a memory as expired or superseded. Provide memory_id from recall results. "
        "Optionally provide replacement_id to chain old → new."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "ID of memory to invalidate."},
            "replacement_id": {"type": "string", "description": "Optional new memory that replaces this one.", "default": ""},
        },
        "required": ["memory_id"],
    },
}

TRIPLE_ADD_SCHEMA = {
    "name": "mnemosyne_triple_add",
    "description": (
        "Add a temporal fact triple (subject, predicate, object) to the knowledge graph. "
        "Example: ('user', 'prefers', 'neovim'). Use for structured relationships."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subject": {"type": "string"},
            "predicate": {"type": "string"},
            "object": {"type": "string"},
            "valid_from": {"type": "string", "description": "ISO date YYYY-MM-DD", "default": ""},
        },
        "required": ["subject", "predicate", "object"],
    },
}

TRIPLE_QUERY_SCHEMA = {
    "name": "mnemosyne_triple_query",
    "description": "Query the temporal knowledge graph for facts matching subject/predicate/object patterns.",
    "parameters": {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "default": ""},
            "predicate": {"type": "string", "default": ""},
            "object": {"type": "string", "default": ""},
        },
    },
}

ALL_TOOL_SCHEMAS = [
    REMEMBER_SCHEMA, RECALL_SCHEMA, SLEEP_SCHEMA, STATS_SCHEMA,
    INVALIDATE_SCHEMA, TRIPLE_ADD_SCHEMA, TRIPLE_QUERY_SCHEMA,
]


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

try:
    from agent.memory_provider import MemoryProvider
except ImportError:
    # Graceful fallback if ABC not available (shouldn't happen in practice)
    MemoryProvider = object  # type: ignore


class MnemosyneMemoryProvider(MemoryProvider):
    """Mnemosyne native memory — local SQLite with vector + FTS5 hybrid search."""

    def __init__(self):
        self._beam: Optional[Any] = None
        self._session_id = "hermes_default"
        self._hermes_home = ""
        self._platform = "cli"
        self._agent_context = "primary"
        self._turn_count = 0
        self._auto_sleep_threshold = 50
        self._auto_sleep_enabled = True

    @property
    def name(self) -> str:
        return "mnemosyne"

    def is_available(self) -> bool:
        """Check if Mnemosyne core is importable. No network calls."""
        try:
            _get_beam_class()
            return True
        except Exception:
            return False

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {"key": "auto_sleep", "description": "Auto-run sleep() when working memory exceeds threshold", "default": True},
            {"key": "sleep_threshold", "description": "Working memory count before auto-sleep triggers", "default": 50},
            {"key": "vector_type", "description": "Vector storage type", "choices": ["float32", "int8", "bit"], "default": "float32"},
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        pass

    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialize Mnemosyne beam for this session."""
        self._agent_context = kwargs.get("agent_context", "primary")
        self._platform = kwargs.get("platform", "cli")
        self._hermes_home = kwargs.get("hermes_home", "")

        if self._agent_context in ("cron", "flush"):
            logger.debug("Mnemosyne skipped: cron/flush context")
            return

        self._session_id = f"hermes_{session_id}"

        # Read config
        try:
            from hermes_cli.config import load_config
            cfg = load_config()
            mn_cfg = cfg.get("memory", {}).get("mnemosyne", {})
            self._auto_sleep_enabled = mn_cfg.get("auto_sleep", True)
            self._auto_sleep_threshold = mn_cfg.get("sleep_threshold", 50)
            vec_type = mn_cfg.get("vector_type", "float32")
            if vec_type:
                os.environ.setdefault("MNEMOSYNE_VEC_TYPE", vec_type)
        except Exception:
            pass

        try:
            BeamMemory = _get_beam_class()
            self._beam = BeamMemory(session_id=self._session_id)
            logger.info("Mnemosyne initialized: session=%s", self._session_id)
        except Exception as e:
            logger.warning("Mnemosyne init failed: %s", e)
            self._beam = None

    def system_prompt_block(self) -> str:
        if not self._beam:
            return ""
        return (
            "# Mnemosyne Memory\n"
            "Active (native local memory). Use mnemosyne_remember to store ANY "
            "durable fact, preference, or insight. Use mnemosyne_recall to search. "
            "The legacy memory tool is deprecated for durable storage — Mnemosyne is primary."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Recall relevant context via Mnemosyne hybrid search."""
        if not self._beam or self._agent_context in ("cron", "flush"):
            return ""
        try:
            results = self._beam.recall(query, top_k=8)
            if not results:
                return ""
            lines = ["## Mnemosyne Context"]
            for r in results:
                content = r.get("content", "")[:200]
                if len(r.get("content", "")) > 200:
                    content += "..."
                ts = r.get("timestamp", "")[:16] if r.get("timestamp") else ""
                imp = r.get("importance", 0.0)
                lines.append(f"  [{ts}] (importance {imp:.2f}) {content}")
            return "\n".join(lines)
        except Exception as e:
            logger.debug("Mnemosyne prefetch failed: %s", e)
            return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        pass

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Persist the turn to Mnemosyne episodic memory."""
        if not self._beam or self._agent_context in ("cron", "flush"):
            return
        try:
            if user_content and len(user_content) > 5:
                self._beam.remember(
                    content=f"[USER] {user_content[:500]}",
                    source="conversation",
                    importance=0.3,
                )
            if assistant_content and len(assistant_content) > 10:
                self._beam.remember(
                    content=f"[ASSISTANT] {assistant_content[:800]}",
                    source="conversation",
                    importance=0.2,
                )
            self._turn_count += 1
            if self._auto_sleep_enabled and self._turn_count % 10 == 0:
                self._maybe_auto_sleep()
        except Exception as e:
            logger.debug("Mnemosyne sync_turn failed: %s", e)

    def _maybe_auto_sleep(self) -> None:
        try:
            stats = self._beam.get_working_stats()
            working = stats.get("count", 0)
            if working > self._auto_sleep_threshold:
                logger.info("Mnemosyne auto-sleep: working=%d > threshold=%d", working, self._auto_sleep_threshold)
                self._beam.sleep()
        except Exception:
            pass

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return tool schemas — static, do not depend on initialization state."""
        return list(ALL_TOOL_SCHEMAS)

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if not self._beam:
            return json.dumps({"error": "Mnemosyne not initialized"})
        try:
            if tool_name == "mnemosyne_remember":
                return self._handle_remember(args)
            elif tool_name == "mnemosyne_recall":
                return self._handle_recall(args)
            elif tool_name == "mnemosyne_sleep":
                return self._handle_sleep(args)
            elif tool_name == "mnemosyne_stats":
                return self._handle_stats(args)
            elif tool_name == "mnemosyne_invalidate":
                return self._handle_invalidate(args)
            elif tool_name == "mnemosyne_triple_add":
                return self._handle_triple_add(args)
            elif tool_name == "mnemosyne_triple_query":
                return self._handle_triple_query(args)
            else:
                return json.dumps({"error": f"Unknown Mnemosyne tool: {tool_name}"})
        except Exception as e:
            logger.error("Mnemosyne tool %s failed: %s", tool_name, e)
            return json.dumps({"error": f"Mnemosyne tool '{tool_name}' failed: {e}"})

    def _handle_remember(self, args: Dict[str, Any]) -> str:
        content = args.get("content", "")
        importance = float(args.get("importance", 0.5))
        source = args.get("source", "user")
        scope = args.get("scope", "session")
        valid_until = args.get("valid_until", None) or None
        if not content:
            return json.dumps({"error": "content is required"})
        memory_id = self._beam.remember(
            content=content,
            importance=importance,
            source=source,
            scope=scope,
            valid_until=valid_until,
        )
        return json.dumps({"status": "stored", "memory_id": memory_id, "content_preview": content[:100]})

    def _handle_recall(self, args: Dict[str, Any]) -> str:
        query = args.get("query", "")
        top_k = int(args.get("limit", 5))
        if not query:
            return json.dumps({"error": "query is required"})
        results = self._beam.recall(query, top_k=top_k)
        return json.dumps({"query": query, "count": len(results), "results": results})

    def _handle_sleep(self, args: Dict[str, Any]) -> str:
        self._beam.sleep()
        working = self._beam.get_working_stats()
        episodic = self._beam.get_episodic_stats()
        return json.dumps({"status": "consolidated", "working": working, "episodic": episodic})

    def _handle_stats(self, args: Dict[str, Any]) -> str:
        working = self._beam.get_working_stats()
        episodic = self._beam.get_episodic_stats()
        return json.dumps({"provider": "mnemosyne", "session_id": self._session_id, "working": working, "episodic": episodic})

    def _handle_invalidate(self, args: Dict[str, Any]) -> str:
        memory_id = args.get("memory_id", "")
        replacement_id = args.get("replacement_id", None) or None
        if not memory_id:
            return json.dumps({"error": "memory_id is required"})
        self._beam.invalidate(memory_id, replacement_id=replacement_id if replacement_id else None)
        return json.dumps({"status": "invalidated", "memory_id": memory_id})

    def _handle_triple_add(self, args: Dict[str, Any]) -> str:
        subject = args.get("subject", "")
        predicate = args.get("predicate", "")
        obj = args.get("object", "")
        valid_from = args.get("valid_from", None) or None
        if not all([subject, predicate, obj]):
            return json.dumps({"error": "subject, predicate, and object are required"})
        add_triple, _ = _get_triple_module()
        triple_id = add_triple(subject, predicate, obj, valid_from=valid_from)
        return json.dumps({"status": "stored", "triple_id": triple_id})

    def _handle_triple_query(self, args: Dict[str, Any]) -> str:
        subject = args.get("subject", "") or None
        predicate = args.get("predicate", "") or None
        obj = args.get("object", "") or None
        _, query_triples = _get_triple_module()
        results = query_triples(subject=subject, predicate=predicate, object=obj)
        return json.dumps({"count": len(results), "results": results})

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        self._turn_count = turn_number

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if not self._beam:
            return
        try:
            logger.info("Mnemosyne session end — running consolidation")
            self._beam.sleep()
        except Exception as e:
            logger.debug("Mnemosyne session-end sleep failed: %s", e)

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        if not self._beam or action not in ("add", "replace"):
            return
        try:
            scope = "global" if target == "user" else "session"
            self._beam.remember(
                content=content,
                source=f"builtin_memory_{target}",
                importance=0.7 if target == "user" else 0.5,
                scope=scope,
            )
        except Exception as e:
            logger.debug("Mnemosyne mirror write failed: %s", e)

    def shutdown(self) -> None:
        self._beam = None


# ---------------------------------------------------------------------------
# Plugin registration (used when loaded via plugins.memory discovery)
# ---------------------------------------------------------------------------

def register_memory_provider(ctx):
    """Called by Hermes memory provider discovery system."""
    provider = MnemosyneMemoryProvider()
    ctx.register_memory_provider(provider)
