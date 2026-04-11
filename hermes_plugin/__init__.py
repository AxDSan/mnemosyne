"""
Mnemosyne Plugin for Hermes
Native memory integration using pre_llm_call hook

This plugin provides seamless memory integration for Hermes agents,
automatically injecting relevant context before every LLM call.
"""

import sys
from pathlib import Path

# Add parent directory to path for importing mnemosyne core
plugin_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(plugin_dir.parent))

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
    Auto-save important tool calls to memory.
    
    This captures tool usage patterns and outcomes for future reference.
    """
    try:
        mem = _get_memory()
        
        # Auto-store important tool calls
        if tool_name in ['terminal', 'execute_code', 'write_file', 'patch']:
            summary = f"Tool {tool_name} executed"
            if args:
                summary += f" with args: {str(args)[:100]}"
            
            mem.remember(
                content=f"[TOOL] {summary}",
                source="tool_execution",
                importance=0.6
            )
    except:
        pass  # Fail silently
