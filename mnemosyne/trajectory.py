"""Normalize Hermes session messages into deterministic trajectory records.

Sleep (and later LCM) consume this stream instead of raw transcripts.
The schema is intentionally small and JSON-serializable. Field names
overlap Letta's ``@letta-ai/trajectory`` where it is cheap (``name``,
``content``, ``ok``, ``id``) so we can ingest theirs later, but this
module does not import that package or add a JS runtime.

Record types
------------
- ``meta``: ``{type, session_id}``
- ``user`` / ``assistant`` / ``reasoning``: ``{type, content, ts?}``
- ``tool_call``: ``{type, name, arguments, id?, ts?}``
- ``tool_result``: ``{type, name?, content, ok?, id?, ts?}``

``ts`` is copied from the source message only. ``normalize`` never
calls ``time.time()`` / ``datetime.now()``, so two calls on the same
input produce identical ``dumps(..., sort_keys=True)`` bytes.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

Record = dict[str, Any]
Message = Mapping[str, Any]


def dumps(records: Sequence[Mapping[str, Any]]) -> str:
    """Canonical JSON: sorted keys, compact separators, no wall-clock."""
    return json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def normalize(
    messages: Sequence[Message],
    *,
    session_id: str = "",
) -> list[Record]:
    """Turn Hermes-shaped messages into an ordered trajectory.

    Input messages use Hermes session fields: ``role``, ``content``,
    ``tool_calls`` (list or JSON string), ``tool_name``, ``tool_call_id``,
    ``reasoning`` / ``reasoning_content``, optional ``timestamp`` / ``ok``.
    """
    records: list[Record] = [{"type": "meta", "session_id": session_id}]
    for message in messages:
        records.extend(_normalize_message(message))
    return records


def _normalize_message(message: Message) -> list[Record]:
    if message.get("active") == 0:
        return []
    role = str(message.get("role") or "").strip().lower()
    ts = _optional_ts(message)
    if role == "user":
        return [_text_record("user", _content_text(message.get("content")), ts)]
    if role == "assistant":
        return _normalize_assistant(message, ts)
    if role == "tool":
        return [_normalize_tool_result(message, ts)]
    return []


def _normalize_assistant(message: Message, ts: Any | None) -> list[Record]:
    records: list[Record] = []
    reasoning = _reasoning_text(message)
    if reasoning:
        records.append(_text_record("reasoning", reasoning, ts))
    content = _content_text(message.get("content"))
    if content:
        records.append(_text_record("assistant", content, ts))
    for call in _tool_calls(message.get("tool_calls")):
        records.append(_normalize_tool_call(call, ts))
    return records


def _normalize_tool_call(call: Mapping[str, Any], ts: Any | None) -> Record:
    raw_function = call.get("function")
    function: Mapping[str, Any] = raw_function if isinstance(raw_function, Mapping) else {}
    name = call.get("name") or function.get("name") or ""
    arguments = _arguments(call.get("arguments", function.get("arguments")))
    record: Record = {
        "type": "tool_call",
        "name": name,
        "arguments": arguments,
    }
    call_id = call.get("id") or call.get("tool_call_id")
    if call_id:
        record["id"] = call_id
    if ts is not None:
        record["ts"] = ts
    return record


def _normalize_tool_result(message: Message, ts: Any | None) -> Record:
    record: Record = {
        "type": "tool_result",
        "content": _content_text(message.get("content")),
    }
    name = message.get("tool_name") or message.get("name")
    if name:
        record["name"] = name
    call_id = message.get("tool_call_id") or message.get("id")
    if call_id:
        record["id"] = call_id
    if "ok" in message:
        record["ok"] = _coerce_ok(message["ok"])
    if ts is not None:
        record["ts"] = ts
    return record


def _text_record(kind: str, content: str, ts: Any | None) -> Record:
    record: Record = {"type": kind, "content": content}
    if ts is not None:
        record["ts"] = ts
    return record


def _optional_ts(message: Message) -> Any | None:
    if "timestamp" in message and message["timestamp"] is not None:
        return message["timestamp"]
    if "ts" in message and message["ts"] is not None:
        return message["ts"]
    return None


def _coerce_ok(value: Any) -> bool:
    if isinstance(value, str) and value.strip().lower() == "false":
        return False
    return bool(value)


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def _reasoning_text(message: Message) -> str:
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _tool_calls(raw: Any) -> list[Mapping[str, Any]]:
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(raw, Mapping):
        return [raw]
    if isinstance(raw, Sequence):
        return [item for item in raw if isinstance(item, Mapping)]
    return []


def _arguments(raw: Any) -> Any:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
        if isinstance(parsed, (dict, list)):
            return parsed
        return raw
    return raw
