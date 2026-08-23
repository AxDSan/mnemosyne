"""Named interactive tool sets for the Hermes memory provider.

Modes are data, not scattered conditionals:

* ``none`` — read-only surface (``READ_TOOLS``)
* ``slim`` — reads plus everyday writes (``READ_TOOLS | SLIM_WRITE_TOOLS``)
* ``full`` — every advertised schema (``ALL_TOOL_SCHEMAS``)

``schemas_for_mode`` looks names up in the provided ``schemas`` list (default:
``hermes_memory_provider.ALL_TOOL_SCHEMAS``) and preserves that inventory's
order. Unknown modes fail loudly.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, List

READ_TOOLS: FrozenSet[str] = frozenset(
    {
        "mnemosyne_recall",
        "mnemosyne_shared_recall",
        "mnemosyne_shared_stats",
        "mnemosyne_stats",
        "mnemosyne_get",
        "mnemosyne_triple_query",
        "mnemosyne_recall_canonical",
        "mnemosyne_model_card",
        "mnemosyne_scratchpad_read",
        "mnemosyne_diagnose",
        "mnemosyne_recall_diagnostics",
        "mnemosyne_graph_query",
        "mnemosyne_sync_status",
        "mnemosyne_persona_list",
    }
)

SLIM_WRITE_TOOLS: FrozenSet[str] = frozenset(
    {
        "mnemosyne_remember",
        "mnemosyne_update",
        "mnemosyne_forget",
        "mnemosyne_invalidate",
        "mnemosyne_batch",
        "mnemosyne_shared_remember",
        "mnemosyne_shared_forget",
    }
)

_MODE_NAME_SETS: Dict[str, FrozenSet[str] | None] = {
    "none": READ_TOOLS,
    "slim": READ_TOOLS | SLIM_WRITE_TOOLS,
    "full": None,
}


def _all_tool_schemas() -> List[Dict[str, Any]]:
    from hermes_memory_provider import ALL_TOOL_SCHEMAS

    return list(ALL_TOOL_SCHEMAS)


def __getattr__(name: str) -> FrozenSet[str]:
    if name == "FULL_TOOLS":
        return frozenset(schema["name"] for schema in _all_tool_schemas())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def schemas_for_mode(
    mode: str,
    schemas: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """Return advertised schemas for ``mode``.

    ``schemas`` defaults to ``hermes_memory_provider.ALL_TOOL_SCHEMAS``. Callers
    with their own inventory (the pip ``mnemosyne_hermes`` provider) pass it
    explicitly so the same name sets filter a different copy.
    """
    if mode not in _MODE_NAME_SETS:
        known = ", ".join(sorted(_MODE_NAME_SETS))
        raise ValueError(
            f"Unknown interactive tool mode {mode!r}. Expected one of: {known}"
        )
    names = _MODE_NAME_SETS[mode]
    inventory = list(schemas) if schemas is not None else _all_tool_schemas()
    if names is None:
        return inventory
    return [schema for schema in inventory if schema["name"] in names]
