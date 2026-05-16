import importlib
import threading
import time


class FakeBeam:
    def __init__(self):
        self.calls = []

    def remember(self, **kwargs):
        self.calls.append(kwargs)
        return f"mem-{len(self.calls)}"


def _provider(monkeypatch, mode, *, async_extract=False):
    monkeypatch.setenv("MNEMOSYNE_AUTOSAVE_USER_MODE", mode)
    monkeypatch.setenv("MNEMOSYNE_AUTOSAVE_EXTRACT_ASYNC", "true" if async_extract else "false")
    module = importlib.import_module("hermes_memory_provider")
    provider = module.MnemosyneMemoryProvider()
    provider._beam = FakeBeam()
    return provider


def test_user_autosave_off_skips_user_turns(monkeypatch):
    provider = _provider(monkeypatch, "off")

    provider.sync_turn("The user prefers Arial for school reports.", "")

    assert provider._beam.calls == []


def test_user_autosave_filtered_skips_chatter_but_saves_durable_preferences(monkeypatch):
    provider = _provider(monkeypatch, "filtered")

    provider.sync_turn("wait what was phase 1 again?", "")
    provider.sync_turn("from now on please call ST1510 Programming for Data Analytics", "")

    assert len(provider._beam.calls) == 1
    call = provider._beam.calls[0]
    assert call["content"] == "[USER] from now on please call ST1510 Programming for Data Analytics"
    assert call["source"] == "conversation"
    assert call["importance"] == 0.3


def test_user_autosave_extract_saves_only_extracted_durable_facts(monkeypatch):
    provider = _provider(monkeypatch, "extract")
    monkeypatch.setattr(
        provider,
        "_extract_user_autosave_memories",
        lambda text: ["The user prefers Programming for Data Analytics to be named fully instead of using ST1510."],
    )

    provider.sync_turn("from now on please call ST1510 Programming for Data Analytics", "")

    assert len(provider._beam.calls) == 1
    call = provider._beam.calls[0]
    assert call["content"] == "The user prefers Programming for Data Analytics to be named fully instead of using ST1510."
    assert call["source"] == "conversation_extract"
    assert call["importance"] == 0.65
    assert call["extract_entities"] is True
    assert call["veracity"] == "stated"


def test_user_autosave_extract_skips_when_extractor_returns_no_facts(monkeypatch):
    provider = _provider(monkeypatch, "extract")
    monkeypatch.setattr(provider, "_extract_user_autosave_memories", lambda text: [])

    provider.sync_turn("thanks salsa", "")

    assert provider._beam.calls == []


def test_user_autosave_extract_async_does_not_block_sync_turn(monkeypatch):
    provider = _provider(monkeypatch, "extract", async_extract=True)
    started = threading.Event()
    release = threading.Event()

    def slow_extract(text):
        started.set()
        release.wait(timeout=2)
        return ["The user prefers memory extraction to run outside the sync-turn path."]

    monkeypatch.setattr(provider, "_extract_user_autosave_memories", slow_extract)

    start = time.perf_counter()
    provider.sync_turn("from now on extract memories without blocking the reply", "")
    elapsed = time.perf_counter() - start

    assert elapsed < 0.2
    assert started.wait(timeout=1)
    assert provider._beam.calls == []

    release.set()
    provider._wait_for_autosave_extractors(timeout=2)

    assert len(provider._beam.calls) == 1
    assert provider._beam.calls[0]["content"] == "The user prefers memory extraction to run outside the sync-turn path."
    assert provider._beam.calls[0]["metadata"] == {"autosave_mode": "extract"}


def test_user_autosave_extract_async_uses_single_worker_queue(monkeypatch):
    provider = _provider(monkeypatch, "extract", async_extract=True)
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()
    release_second = threading.Event()

    def slow_extract(text):
        if "first" in text:
            first_started.set()
            release_first.wait(timeout=2)
            return ["The user prefers the first queued memory to complete before the second starts."]
        if "second" in text:
            second_started.set()
            release_second.wait(timeout=2)
            return ["The user prefers autosave extraction jobs to run serially."]
        return []

    monkeypatch.setattr(provider, "_extract_user_autosave_memories", slow_extract)

    provider.sync_turn("from now on remember first queued extraction", "")
    provider.sync_turn("from now on remember second queued extraction", "")

    assert first_started.wait(timeout=1)
    assert not second_started.wait(timeout=0.1)
    assert provider._beam.calls == []

    release_first.set()
    assert second_started.wait(timeout=1)
    release_second.set()
    provider._wait_for_autosave_extractors(timeout=2)

    assert [call["content"] for call in provider._beam.calls] == [
        "The user prefers the first queued memory to complete before the second starts.",
        "The user prefers autosave extraction jobs to run serially.",
    ]


def test_memory_autosave_extraction_parser_accepts_json_array(monkeypatch):
    provider = _provider(monkeypatch, "extract")

    parsed = provider._parse_user_autosave_extraction(
        '["The user prefers Arial for school reports.", "The user wants plain English explanations for school work."]'
    )

    assert parsed == [
        "The user prefers Arial for school reports.",
        "The user wants plain English explanations for school work.",
    ]


def test_memory_autosave_extraction_parser_rejects_meta_and_empty(monkeypatch):
    provider = _provider(monkeypatch, "extract")

    assert provider._parse_user_autosave_extraction("NO_MEMORY") == []
    assert provider._parse_user_autosave_extraction('["The assistant should review the conversation above."]') == []


def test_user_autosave_prompt_is_generic_and_caps_input(monkeypatch):
    provider = _provider(monkeypatch, "extract")

    prompt = provider._user_autosave_prompt("x" * 10000)

    assert "Alice" not in prompt
    assert "ExampleAssistant" not in prompt
    user_section = prompt.split("User message:\n", 1)[1].rstrip("\n")
    assert len(user_section) == 4000
    assert set(user_section) == {"x"}
    assert "third-person memory about the user" in prompt
