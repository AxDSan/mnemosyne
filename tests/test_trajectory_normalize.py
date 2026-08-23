"""Hermes session messages normalize to a deterministic trajectory.

Sleep will later consume these records. This module only pins the
normalizer: same fixture bytes must dump identically, and timestamps
appear only when the source message already had one.
"""

from __future__ import annotations

import json

from mnemosyne.trajectory import dumps, normalize


SESSION_ID = "sess-fixture-1"

# 1 user + 1 assistant (content + one tool_call) + 1 tool_result.
# Timestamps are source-message values, not wall-clock.
FIXTURE_MESSAGES = [
    {
        "role": "user",
        "content": "List the repo root.",
        "timestamp": 1_700_000_000.0,
    },
    {
        "role": "assistant",
        "content": "I'll list the directory.",
        "reasoning_content": "Need a directory listing before answering.",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "terminal",
                    "arguments": '{"command": "ls"}',
                },
            }
        ],
        "timestamp": 1_700_000_001.0,
    },
    {
        "role": "tool",
        "tool_name": "terminal",
        "tool_call_id": "call_1",
        "content": "mnemosyne\ntests",
        "ok": True,
        "timestamp": 1_700_000_002.0,
    },
]


def test_normalize_fixture_emits_expected_record_types():
    records = normalize(FIXTURE_MESSAGES, session_id=SESSION_ID)
    types = [record["type"] for record in records]
    assert types == [
        "meta",
        "user",
        "reasoning",
        "assistant",
        "tool_call",
        "tool_result",
    ]


def test_normalize_fixture_fields():
    records = normalize(FIXTURE_MESSAGES, session_id=SESSION_ID)
    by_type = {record["type"]: record for record in records}

    assert by_type["meta"] == {"type": "meta", "session_id": SESSION_ID}
    assert by_type["user"] == {
        "type": "user",
        "content": "List the repo root.",
        "ts": 1_700_000_000.0,
    }
    assert by_type["reasoning"] == {
        "type": "reasoning",
        "content": "Need a directory listing before answering.",
        "ts": 1_700_000_001.0,
    }
    assert by_type["assistant"] == {
        "type": "assistant",
        "content": "I'll list the directory.",
        "ts": 1_700_000_001.0,
    }
    assert by_type["tool_call"] == {
        "type": "tool_call",
        "name": "terminal",
        "arguments": {"command": "ls"},
        "id": "call_1",
        "ts": 1_700_000_001.0,
    }
    assert by_type["tool_result"] == {
        "type": "tool_result",
        "name": "terminal",
        "content": "mnemosyne\ntests",
        "ok": True,
        "id": "call_1",
        "ts": 1_700_000_002.0,
    }


def test_normalize_is_deterministic_across_calls():
    first = dumps(normalize(FIXTURE_MESSAGES, session_id=SESSION_ID))
    second = dumps(normalize(FIXTURE_MESSAGES, session_id=SESSION_ID))
    assert first == second
    # Canonical dump: sorted keys, no generated wall-clock fields.
    parsed = json.loads(first)
    for record in parsed:
        if "ts" in record:
            assert record["ts"] in (
                1_700_000_000.0,
                1_700_000_001.0,
                1_700_000_002.0,
            )


def test_normalize_omits_ts_when_source_has_none():
    records = normalize(
        [{"role": "user", "content": "hello"}],
        session_id="no-ts",
    )
    user = next(record for record in records if record["type"] == "user")
    assert user == {"type": "user", "content": "hello"}
    assert all("ts" not in record for record in records)


def test_normalize_omits_ok_when_source_has_none():
    records = normalize(
        [
            {
                "role": "tool",
                "tool_name": "terminal",
                "tool_call_id": "call_x",
                "content": "ok",
            }
        ],
        session_id="no-ok",
    )
    result = next(record for record in records if record["type"] == "tool_result")
    assert "ok" not in result
    assert result["id"] == "call_x"
    assert result["name"] == "terminal"


def test_normalize_tool_calls_from_json_string():
    # state.db stores tool_calls as TEXT; rows arrive as a JSON string.
    raw = json.dumps(
        [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "terminal",
                    "arguments": '{"command": "ls"}',
                },
            }
        ]
    )
    records = normalize(
        [{"role": "assistant", "content": "", "tool_calls": raw}],
        session_id="json-string",
    )
    tool_calls = [record for record in records if record["type"] == "tool_call"]
    assert tool_calls == [
        {
            "type": "tool_call",
            "name": "terminal",
            "arguments": {"command": "ls"},
            "id": "call_1",
        }
    ]


def test_normalize_malformed_tool_calls_json_emits_no_tool_call():
    records = normalize(
        [{"role": "assistant", "content": "hi", "tool_calls": "{not-json"}],
        session_id="bad-json",
    )
    assert [record["type"] for record in records] == ["meta", "assistant"]


def test_normalize_tool_call_missing_name_arguments_and_id():
    records = normalize(
        [{"role": "assistant", "content": "", "tool_calls": [{}]}],
        session_id="missing-fields",
    )
    tool_calls = [record for record in records if record["type"] == "tool_call"]
    assert tool_calls == [{"type": "tool_call", "name": "", "arguments": {}}]


def test_normalize_ok_string_false_is_false():
    records = normalize(
        [
            {
                "role": "tool",
                "tool_name": "terminal",
                "tool_call_id": "call_x",
                "content": "err",
                "ok": "false",
            }
        ],
        session_id="ok-false",
    )
    result = next(record for record in records if record["type"] == "tool_result")
    assert result["ok"] is False
