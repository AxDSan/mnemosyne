"""Sleep upserts durable preference/identity/environment canonical slots.

Letta sleeptime edits shared blocks. Mnemosyne sleep already proposes
canonical updates via model_refresh; this suite pins that those proposals
may land in durable human-facing slots (not only model:*) and that slot
bodies stay compact facts, never transcript dumps or SOUL.md writes.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from mnemosyne.core.beam import BeamMemory
from mnemosyne.core.canonical import CanonicalStore
from mnemosyne.core import model_refresh


FIXTURE_WM_1 = "user prefers uv run for Python commands and tests"
FIXTURE_WM_2 = "when installing packages the user prefers uv run over pip"


def _old_rows(db_path: Path, rows: list[tuple[str, str]], session_id: str = "sleep-slots") -> None:
    old_ts = (datetime.now() - timedelta(hours=200)).isoformat()
    conn = sqlite3.connect(db_path)
    for memory_id, content in rows:
        conn.execute(
            "INSERT INTO working_memory (id, content, source, timestamp, session_id) VALUES (?, ?, ?, ?, ?)",
            (memory_id, content, "conversation", old_ts, session_id),
        )
    conn.commit()
    conn.close()


def _preference_python_json(*, body: str = "User prefers uv run for Python.") -> str:
    return json.dumps(
        [
            {
                "category": "preference",
                "name": "python",
                "body": body,
                "confidence": 0.95,
                "evidence_ids": ["wm-uv-1", "wm-uv-2"],
                "action": "update",
                "reason": "Repeated durable preference.",
            }
        ]
    )


def test_parse_accepts_preference_identity_environment_slots():
    for category, name, body in (
        ("preference", "python", "User prefers uv run for Python."),
        ("identity", "name", "The operator goes by Tim."),
        ("environment", "os", "Development happens on macOS."),
    ):
        raw = json.dumps(
            [
                {
                    "category": category,
                    "name": name,
                    "body": body,
                    "confidence": 0.95,
                    "evidence_ids": ["wm-a", "wm-b"],
                    "action": "update",
                    "reason": "Durable fact.",
                }
            ]
        )
        parsed = model_refresh.parse_model_update_proposals(raw)
        assert len(parsed) == 1, f"{category} should be an allowed sleep slot"
        assert parsed[0]["category"] == category
        assert parsed[0]["name"] == name
        assert parsed[0]["body"] == body


def test_parse_drops_oversized_slot_body():
    cap = model_refresh.MAX_CANONICAL_SLOT_BODY_CHARS
    raw = json.dumps(
        [
            {
                "category": "preference",
                "name": "python",
                "body": "x" * (cap + 1),
                "confidence": 0.99,
                "evidence_ids": ["wm-a", "wm-b"],
                "action": "update",
                "reason": "Too long.",
            }
        ]
    )
    assert model_refresh.parse_model_update_proposals(raw) == []


def test_parse_drops_transcript_like_slot_body():
    transcript = (
        "User: user prefers uv run\n"
        "Assistant: Got it, I'll use uv run from now on.\n"
        "User: also use uv run pytest\n"
        "Assistant: Sure."
    )
    raw = json.dumps(
        [
            {
                "category": "preference",
                "name": "python",
                "body": transcript,
                "confidence": 0.99,
                "evidence_ids": ["wm-a", "wm-b"],
                "action": "update",
                "reason": "Chat dump.",
            }
        ]
    )
    assert model_refresh.parse_model_update_proposals(raw) == []


def test_sleep_upserts_preference_python_from_fixture(tmp_path, monkeypatch):
    from mnemosyne.core import local_llm

    db_path = tmp_path / "mnemo.db"
    beam = BeamMemory(session_id="sleep-slots", db_path=db_path)
    _old_rows(
        db_path,
        [
            ("wm-uv-1", FIXTURE_WM_1),
            ("wm-uv-2", FIXTURE_WM_2),
        ],
    )

    compact = "User prefers uv run for Python commands."
    host = MagicMock(return_value=(True, _preference_python_json(body=compact)))
    remote = MagicMock()
    monkeypatch.setattr(local_llm, "_try_host_llm", host)
    monkeypatch.setattr(local_llm, "_call_remote_llm", remote)

    result = beam.sleep(dry_run=False)
    assert result["status"] == "consolidated"
    assert result["model_refresh"]["applied"] >= 1
    host.assert_called()
    remote.assert_not_called()

    store = CanonicalStore(db_path=db_path, conn=beam.conn)
    slot = store.recall("default", "preference", "python")
    assert slot is not None
    assert "uv run" in slot["body"]
    assert compact == slot["body"] or slot["body"].startswith("User prefers uv run")
    assert FIXTURE_WM_1 not in slot["body"]
    assert FIXTURE_WM_2 not in slot["body"]
    assert "User:" not in slot["body"]
    assert "Assistant:" not in slot["body"]
    assert len(slot["body"]) <= model_refresh.MAX_CANONICAL_SLOT_BODY_CHARS


def test_sleep_drops_oversized_and_transcript_proposals(tmp_path, monkeypatch):
    from mnemosyne.core import local_llm

    db_path = tmp_path / "mnemo.db"
    beam = BeamMemory(session_id="sleep-slots", db_path=db_path)
    _old_rows(
        db_path,
        [
            ("wm-uv-1", FIXTURE_WM_1),
            ("wm-uv-2", FIXTURE_WM_2),
        ],
    )

    dump = (
        f"User: {FIXTURE_WM_1}\n"
        f"Assistant: noted\n"
        f"User: {FIXTURE_WM_2}\n"
        "Assistant: I will remember the entire transcript."
    )
    oversized = "y" * (model_refresh.MAX_CANONICAL_SLOT_BODY_CHARS + 40)
    payload = json.dumps(
        [
            {
                "category": "preference",
                "name": "python",
                "body": dump,
                "confidence": 0.99,
                "evidence_ids": ["wm-uv-1", "wm-uv-2"],
                "action": "update",
                "reason": "Transcript dump.",
            },
            {
                "category": "identity",
                "name": "bio",
                "body": oversized,
                "confidence": 0.99,
                "evidence_ids": ["wm-uv-1", "wm-uv-2"],
                "action": "update",
                "reason": "Oversized.",
            },
        ]
    )
    monkeypatch.setattr(local_llm, "_try_host_llm", MagicMock(return_value=(True, payload)))
    monkeypatch.setattr(local_llm, "_call_remote_llm", MagicMock())

    result = beam.sleep(dry_run=False)
    assert result["status"] == "consolidated"
    assert result["model_refresh"]["proposals"] == 0
    assert result["model_refresh"]["applied"] == 0

    store = CanonicalStore(db_path=db_path, conn=beam.conn)
    assert store.recall("default", "preference", "python") is None
    assert store.recall("default", "identity", "bio") is None


def test_sleep_does_not_write_soul_md(tmp_path, monkeypatch):
    from mnemosyne.core import local_llm

    db_path = tmp_path / "mnemo.db"
    soul = tmp_path / "SOUL.md"
    monkeypatch.chdir(tmp_path)

    beam = BeamMemory(session_id="sleep-slots", db_path=db_path)
    _old_rows(
        db_path,
        [
            ("wm-uv-1", FIXTURE_WM_1),
            ("wm-uv-2", FIXTURE_WM_2),
        ],
    )
    monkeypatch.setattr(
        local_llm,
        "_try_host_llm",
        MagicMock(return_value=(True, _preference_python_json())),
    )
    monkeypatch.setattr(local_llm, "_call_remote_llm", MagicMock())

    beam.sleep(dry_run=False)
    assert not soul.exists()
    assert not (tmp_path / ".hermes" / "SOUL.md").exists()
    assert list(tmp_path.rglob("SOUL.md")) == []


def test_model_refresh_prompt_documents_slot_contract():
    prompt = model_refresh.build_model_refresh_prompt(
        [{"id": "wm-uv-1", "content": FIXTURE_WM_1}]
    )
    assert "preference" in prompt
    assert "identity" in prompt
    assert "environment" in prompt
    assert str(model_refresh.MAX_CANONICAL_SLOT_BODY_CHARS) in prompt
    assert "transcript" in prompt.lower()
    assert "SOUL.md" in prompt


def test_consolidation_system_prompt_omits_slot_body_rules():
    text = model_refresh.consolidation_system_prompt()
    assert "memory consolidation engine" in text.lower()
    assert "400" not in text
    assert "transcript" not in text.lower()
