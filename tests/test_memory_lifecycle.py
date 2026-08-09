"""Regression coverage for the stable Mnemosyne construction contract."""

from mnemosyne.core.memory import Mnemosyne


def test_mnemosyne_initializes_beam_and_can_remember(tmp_path):
    memory = Mnemosyne(session_id="lifecycle", db_path=tmp_path / "memory.db")

    assert memory.beam is not None
    memory_id = memory.remember("lifecycle smoke test", source="test")
    assert isinstance(memory_id, str)


def test_invalidate_emits_wrapper_event_only_after_success(tmp_path, monkeypatch):
    memory = Mnemosyne(session_id="lifecycle", db_path=tmp_path / "memory.db")
    events = []
    monkeypatch.setattr(memory, "_emit_wrapper", lambda *args, **kwargs: events.append((args, kwargs)))

    monkeypatch.setattr(memory.beam, "invalidate", lambda *args, **kwargs: True)
    assert memory.invalidate("present", replacement_id="replacement") is True

    monkeypatch.setattr(memory.beam, "invalidate", lambda *args, **kwargs: False)
    assert memory.invalidate("missing") is False

    assert events == [
        (("MEMORY_INVALIDATED", "present"), {"replacement_id": "replacement"}),
    ]
