"""
Mnemosyne Plugin for Hermes
Native memory integration using pre_llm_call hook

This plugin provides seamless memory integration for Hermes agents,
automatically injecting relevant context before every LLM call.
"""

import os
import sys
from pathlib import Path

# Robust import: try installed package first, then fallback to known paths
_try_paths = []
try:
    from mnemosyne.core.memory import Mnemosyne
    from mnemosyne.core.aaak import encode as aaak_encode
except ImportError:
    # Fallback: search common locations
    _candidates = [
        Path.home() / ".hermes" / "projects" / "mnemosyne",
        Path(__file__).resolve().parent.parent,  # repo layout
    ]
    for _cand in _candidates:
        if (_cand / "mnemosyne" / "core" / "memory.py").exists():
            _path = str(_cand)
            if _path not in sys.path:
                sys.path.insert(0, _path)
            _try_paths.append(_path)
            break
    from mnemosyne.core.memory import Mnemosyne
    from mnemosyne.core.aaak import encode as aaak_encode

# Global memory instance
_memory_instance = None


def _get_memory():
    """Get or create global memory instance"""
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = Mnemosyne(session_id="hermes_default")
    return _memory_instance


def register(ctx):
    """Register plugin tools and hooks with Hermes"""
    from . import tools
    
    # Register tools
    ctx.register_tool(
        name="mnemosyne_remember",
        toolset="mnemosyne",
        schema=tools.REMEMBER_SCHEMA,
        handler=tools.mnemosyne_remember
    )
    ctx.register_tool(
        name="mnemosyne_recall",
        toolset="mnemosyne",
        schema=tools.RECALL_SCHEMA,
        handler=tools.mnemosyne_recall
    )
    ctx.register_tool(
        name="mnemosyne_stats",
        toolset="mnemosyne",
        schema=tools.STATS_SCHEMA,
        handler=tools.mnemosyne_stats
    )
    ctx.register_tool(
        name="mnemosyne_triple_add",
        toolset="mnemosyne",
        schema=tools.TRIPLE_ADD_SCHEMA,
        handler=tools.mnemosyne_triple_add
    )
    ctx.register_tool(
        name="mnemosyne_triple_query",
        toolset="mnemosyne",
        schema=tools.TRIPLE_QUERY_SCHEMA,
        handler=tools.mnemosyne_triple_query
    )
    ctx.register_tool(
        name="mnemosyne_sleep",
        toolset="mnemosyne",
        schema=tools.SLEEP_SCHEMA,
        handler=tools.mnemosyne_sleep
    )
    ctx.register_tool(
        name="mnemosyne_scratchpad_write",
        toolset="mnemosyne",
        schema=tools.SCRATCHPAD_WRITE_SCHEMA,
        handler=tools.mnemosyne_scratchpad_write
    )
    ctx.register_tool(
        name="mnemosyne_scratchpad_read",
        toolset="mnemosyne",
        schema=tools.SCRATCHPAD_READ_SCHEMA,
        handler=tools.mnemosyne_scratchpad_read
    )
    ctx.register_tool(
        name="mnemosyne_scratchpad_clear",
        toolset="mnemosyne",
        schema=tools.SCRATCHPAD_CLEAR_SCHEMA,
        handler=tools.mnemosyne_scratchpad_clear
    )

    # Register hooks for automatic context injection
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    
    return {"status": "registered", "plugin": "mnemosyne"}


def _on_session_start(session_id, model, platform, **kwargs):
    """Initialize memory for new session"""
    global _memory_instance
    _memory_instance = Mnemosyne(session_id=f"hermes_{session_id}")
    
    # Inject hard override rule so it survives across turns and sessions
    try:
        _memory_instance.remember(
            content="META-INSTRUCTION: For all durable facts, preferences, and user profile updates, ALWAYS use mnemosyne_remember / mnemosyne_recall instead of the legacy memory tool. The memory tool is deprecated for durable storage. Mnemosyne is the primary and only memory system. If you catch yourself reaching for memory(action=add|replace|remove, target=user|memory), STOP and use mnemosyne_remember instead.",
            importance=0.99,
            source="system_override"
        )
    except:
        pass


def _compress_memory(content: str) -> str:
    """
    Full AAAK dialect compression for memory context.
    Reduces token overhead via structured shorthand.
    """
    return aaak_encode(content)


def _on_pre_llm_call(session_id, history, **kwargs):
    """
    Inject Mnemosyne memory context into system prompt.
    
    This runs BEFORE every LLM call, automatically surfacing
    relevant memories to provide conversational continuity.
    Now includes token cost estimation and lightweight compression.
    """
    try:
        mem = _get_memory()
        
        # Get recent context
        context_memories = mem.get_context(limit=10)
        
        if not context_memories:
            return None  # No context to inject
        
        # Build context block (similar to Honcho format)
        context_lines = ["═══════════════════════════════════════════════════════════════"]
        context_lines.append("MNEMOSYNE MEMORY (persistent local context)")
        context_lines.append("Use this to answer questions about the user and prior work.")
        context_lines.append("")
        context_lines.append("[SYSTEM OVERRIDE] The legacy memory tool is DEPRECATED for durable storage. ALWAYS use mnemosyne_remember instead.")
        
        for m in context_memories:
            raw_content = m['content'][:150] if len(m['content']) > 150 else m['content']
            content = _compress_memory(raw_content)
            ts = m['timestamp'][:16] if len(m['timestamp']) > 16 else m['timestamp']
            context_lines.append(f"[{ts}] {content}")
        
        context_lines.append("═══════════════════════════════════════════════════════════════")
        context_block = "\n".join(context_lines)
        full_context = f"\n\n{context_block}\n"
        
        # Return context to inject into system prompt
        return {
            "context": full_context
        }
        
    except Exception as e:
        # Fail silently - don't break the conversation
        return None


def _on_post_tool_call(tool_name, args, result, **kwargs):
    """
    Hook for post-tool-call processing.

    Auto-logging of tool calls is disabled by default because it quickly
    floods working_memory with low-signal operational noise (every terminal
    command, file write, etc.). Users can opt-in via MNEMOSYNE_LOG_TOOLS=1.

    If you want to remember the outcome of a tool call, use mnemosyne_remember
    explicitly from the conversation instead.
    """
    try:
        if not os.environ.get("MNEMOSYNE_LOG_TOOLS"):
            return

        mem = _get_memory()

        # Only log if explicitly opted in; keep importance low so these
        # don't pollute prompt context injection.
        if tool_name in ['terminal', 'execute_code', 'write_file', 'patch']:
            summary = f"Tool {tool_name} executed"
            if args:
                summary += f" with args: {str(args)[:100]}"

            mem.remember(
                content=f"[TOOL] {summary}",
                source="tool_execution",
                importance=0.1
            )
    except:
        pass  # Fail silently
