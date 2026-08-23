"""Regression tests for issue #832 / B15: batch error payloads are sanitized."""

import json

import pytest

from mnemosyne.batch_tool import (
    BatchValidationError,
    batch_validation_error_payload,
    validate_batch_operations,
)


class _FakeBeam:
    conn = None

    def remember(self, **kwargs):
        return "fake-id"


def test_validation_payload_contains_no_raw_message():
    exc = BatchValidationError(
        "content is required\n/private/secret/path", 0, "remember"
    )
    payload = batch_validation_error_payload(exc)
    assert payload == {
        "status": "error",
        "error": "batch_validation_failed",
        "failed_index": 0,
        "action": "remember",
    }


def test_unknown_action_not_reflected():
    with pytest.raises(BatchValidationError) as exc_info:
        validate_batch_operations([{"action": "<script>alert(1)</script>"}])
    payload = batch_validation_error_payload(exc_info.value)
    assert payload["action"] is None
    assert "<script>" not in json.dumps(payload)


def test_execution_failure_excludes_raw_exception_text(tmp_path):
    canary = "/private/secret/path\nmultiline\ncanary"

    class _FakeBeam:
        conn = None

    beam = _FakeBeam()
    normalized = [{"index": 0, "action": "remember", "payload": {"content": "x"}}]

    from unittest.mock import patch
    import mnemosyne.batch_tool as bt

    with (
        patch.object(bt, "_deferred_commits", lambda conn: __import__("contextlib", fromlist=["nullcontext"]).nullcontext()),
        patch.object(bt, "_apply_one", side_effect=RuntimeError(canary)),
    ):
        result = bt.apply_beam_batch(beam, normalized)

    assert result["status"] == "error"
    assert result["error"] == "batch_failed"
    assert result["failed_index"] == 0
    assert canary not in json.dumps(result)
    assert "RuntimeError" not in json.dumps(result)
    assert "\n" not in result.get("error", "")