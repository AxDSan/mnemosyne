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
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

Record = dict[str, Any]
Message = Mapping[str, Any]

# Count line printed by ``hermes mnemosyne sleep --dry-run``.
COUNT_TYPES = ("user", "assistant", "tool_call", "tool_result")

# Default when --session is omitted: most recently active Hermes session.
DEFAULT_SESSION = "latest"

_TOOL_XML_BLOCK_RE = re.compile(
    r"</?tool_calls?>",
    re.IGNORECASE,
)
_TOOL_XML_PAIR_RE = re.compile(
    r"<tool_calls?\b[^>]*>.*?</tool_calls?>",
    re.IGNORECASE | re.DOTALL,
)

# Test hook: inject messages so CI never opens ~/.hermes/state.db.
_injected_session_messages: Sequence[Message] | None = None


def set_injected_session_messages(messages: Sequence[Message] | None) -> None:
    """Replace (or clear) the in-process session-message override."""
    global _injected_session_messages
    _injected_session_messages = messages


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


def record_counts(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Count user/assistant/tool_call/tool_result records (ignore meta/reasoning)."""
    counts = {kind: 0 for kind in COUNT_TYPES}
    for record in records:
        kind = record.get("type")
        if kind in counts:
            counts[kind] += 1
    return counts


def from_messages(
    messages: Sequence[Message],
    *,
    session_id: str = "",
) -> tuple[list[Record], dict[str, int]]:
    """Normalize session messages and return (records, type counts)."""
    records = normalize(messages, session_id=session_id)
    return records, record_counts(records)


def has_session_trajectory(records: Sequence[Mapping[str, Any]] | None) -> bool:
    """True when records include user/assistant/tool turns (not meta-only)."""
    if not records:
        return False
    return any(record.get("type") in COUNT_TYPES for record in records)


def attach_sleep_trajectory(
    beam: Any,
    *,
    session_id: str | None = None,
    messages: Sequence[Message] | None = None,
) -> None:
    """Attach usable session records onto ``beam.sleep_trajectory_records``.

    Meta-only / empty sessions are left unset so sleep falls back to
    working-memory for model-refresh.
    """
    try:
        if messages is not None:
            records, _counts = from_messages(messages, session_id=session_id or "")
        else:
            records, _counts = resolve_sleep_trajectory(session_id)
        if has_session_trajectory(records):
            beam.sleep_trajectory_records = records
    except Exception:
        pass


def format_count_line(counts: Mapping[str, int] | None) -> str:
    """Dry-run line after the aux slot. Never includes raw tool XML."""
    if counts is None:
        return "trajectory: unavailable"
    return (
        "trajectory: "
        f"user={int(counts.get('user', 0))} "
        f"assistant={int(counts.get('assistant', 0))} "
        f"tool_call={int(counts.get('tool_call', 0))} "
        f"tool_result={int(counts.get('tool_result', 0))}"
    )


def _strip_tool_xml(text: str) -> str:
    cleaned = _TOOL_XML_PAIR_RE.sub("", text)
    cleaned = _TOOL_XML_BLOCK_RE.sub("", cleaned)
    return cleaned.strip()


def _sanitize_record(record: Mapping[str, Any]) -> Record:
    sanitized: Record = {}
    for key, value in record.items():
        if isinstance(value, str):
            sanitized[key] = _strip_tool_xml(value)
        else:
            sanitized[key] = value
    return sanitized


def sleep_prompt_items(records: Sequence[Mapping[str, Any]]) -> list[Record]:
    """Model-refresh items: compact JSON records, never raw tool XML."""
    items: list[Record] = []
    for index, record in enumerate(records):
        if record.get("type") == "meta":
            continue
        clean = _sanitize_record(record)
        items.append(
            {
                "id": str(clean.get("id") or f"traj-{index}"),
                "content": dumps([clean]),
            }
        )
    return items


def resolve_sleep_trajectory(
    session_id: str | None = None,
    *,
    messages: Sequence[Message] | None = None,
) -> tuple[list[Record] | None, dict[str, int] | None]:
    """Load messages (injected, explicit, or state.db) and normalize.

    Returns ``(None, None)`` when no session messages are available so
    callers can print ``trajectory: unavailable`` instead of crashing.
    Default session is the most recently active Hermes session
    (``DEFAULT_SESSION`` / ``latest``).
    """
    try:
        loaded = messages
        if loaded is None:
            loaded = _injected_session_messages
        if loaded is None:
            loaded = _load_hermes_session_messages(session_id)
        if not loaded:
            return None, None
        sid = "" if not session_id or session_id == DEFAULT_SESSION else session_id
        records, counts = from_messages(loaded, session_id=sid)
        if not has_session_trajectory(records):
            return None, None
        return records, counts
    except Exception:
        return None, None


def _hermes_state_db() -> Path | None:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    path = Path(home) / "state.db"
    return path if path.is_file() else None


def _latest_hermes_session_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT id FROM sessions "
        "ORDER BY COALESCE(last_activity_at, started_at, 0) DESC "
        "LIMIT 1"
    ).fetchone()
    return None if row is None else row["id"]


def _session_has_messages(conn: sqlite3.Connection, sid: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM messages WHERE session_id = ? LIMIT 1",
        (sid,),
    ).fetchone()
    return row is not None


def _messages_for_session(conn: sqlite3.Connection, sid: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT role, content, tool_calls, tool_name, tool_call_id, "
        "reasoning, reasoning_content, timestamp, id, active "
        "FROM messages WHERE session_id = ? ORDER BY id ASC",
        (sid,),
    ).fetchall()


def _resolve_state_db_session_id(
    conn: sqlite3.Connection,
    session_id: str | None,
) -> str | None:
    """Map Beam/provider ids onto Hermes ``state.db`` session ids.

    ``beam.session_id`` is ``hermes_{scope}``; ``messages.session_id`` is
    the raw Hermes id (``20260823_…``). Strip a ``hermes_`` prefix and
    retry. Unknown ``hermes_*`` ids fall back to latest, same as omitting
    ``--session``.
    """
    if not session_id or session_id == DEFAULT_SESSION:
        return _latest_hermes_session_id(conn)

    candidates = [session_id]
    if session_id.startswith("hermes_"):
        stripped = session_id[len("hermes_") :]
        if stripped and stripped not in candidates:
            candidates.append(stripped)

    for sid in candidates:
        if _session_has_messages(conn, sid):
            return sid

    if session_id.startswith("hermes_"):
        return _latest_hermes_session_id(conn)
    return session_id


def _load_hermes_session_messages(session_id: str | None) -> list[Record] | None:
    """Best-effort read of Hermes ``state.db``. Never raises to callers."""
    db_path = _hermes_state_db()
    if db_path is None:
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        sid = _resolve_state_db_session_id(conn, session_id)
        if not sid:
            return None
        rows = _messages_for_session(conn, sid)
        if not rows:
            return None
        messages: list[Record] = []
        for row in rows:
            message = {key: row[key] for key in row.keys() if row[key] is not None}
            messages.append(message)
        return messages
    except Exception:
        return None
    finally:
        conn.close()

