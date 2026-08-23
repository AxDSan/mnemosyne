"""Tests for the Hermes auxiliary LLM adapter."""

from __future__ import annotations

import sys
import types

import pytest

from mnemosyne.core.llm_backends import get_host_llm_backend


# ---------------------------------------------------------------------------
# Fake `agent` package — Hermes is not a test-time dependency.
# Patching `agent.auxiliary_client.call_llm` requires the dotted path to exist
# in sys.modules before unittest.mock can resolve it.
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_agent_module(monkeypatch):
    """Inject a fake ``agent.auxiliary_client`` into sys.modules.

    Yields the auxiliary_client submodule so tests can attach call_llm /
    extract_content_or_reasoning mocks. Module is scrubbed on teardown so
    later tests see a clean slate.
    """
    agent_pkg = types.ModuleType("agent")
    aux_client = types.ModuleType("agent.auxiliary_client")
    agent_pkg.auxiliary_client = aux_client  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "agent", agent_pkg)
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", aux_client)
    yield aux_client


def _import_adapter():
    # Lazy import so the module-level adapter never references a missing
    # ``agent.*`` at collection time.
    from hermes_memory_provider import hermes_llm_adapter
    return hermes_llm_adapter


# ---------------------------------------------------------------------------
# resolve_sleep_aux_task()
# ---------------------------------------------------------------------------

def test_resolve_sleep_aux_task_defaults_to_compression_without_sleep_slot():
    adapter = _import_adapter()
    resolved = adapter.resolve_sleep_aux_task({"auxiliary": {"compression": {"model": "gemini-flash"}}})
    assert resolved.task == "compression"
    assert resolved.model == "gemini-flash"


def test_resolve_sleep_aux_task_uses_sleep_when_model_set():
    adapter = _import_adapter()
    resolved = adapter.resolve_sleep_aux_task(
        {"auxiliary": {"sleep": {"model": "grok-4.6"}, "compression": {"model": "gemini-flash"}}}
    )
    assert resolved.task == "sleep"
    assert resolved.model == "grok-4.6"


def test_resolve_sleep_aux_task_uses_sleep_when_provider_set():
    adapter = _import_adapter()
    resolved = adapter.resolve_sleep_aux_task({"auxiliary": {"sleep": {"provider": "xai-oauth"}}})
    assert resolved.task == "sleep"
    assert resolved.provider == "xai-oauth"


def test_resolve_sleep_aux_task_timeout_only_sleep_slot_stays_compression():
    adapter = _import_adapter()
    resolved = adapter.resolve_sleep_aux_task({"auxiliary": {"sleep": {"timeout": 300}}})
    assert resolved.task == "compression"


def test_resolve_sleep_aux_task_missing_config_does_not_raise(monkeypatch):
    adapter = _import_adapter()
    monkeypatch.setattr(adapter, "_load_hermes_config", lambda: {})
    assert adapter.resolve_sleep_aux_task(None).task == "compression"
    assert adapter.resolve_sleep_aux_task({}).task == "compression"
    assert adapter.resolve_sleep_aux_task(object()).task == "compression"
    assert adapter.resolve_sleep_aux_task({"auxiliary": "nope"}).task == "compression"
    monkeypatch.setattr(adapter, "_load_hermes_config", lambda: (_ for _ in ()).throw(RuntimeError("no config")))
    assert adapter.resolve_sleep_aux_task().task == "compression"


# ---------------------------------------------------------------------------
# HermesAuxLLMBackend.complete()
# ---------------------------------------------------------------------------

def test_complete_calls_call_llm_with_compression_task(fake_agent_module, monkeypatch):
    """Adapter must invoke call_llm(task='compression', ...) with passed args.

    Isolated from live ~/.hermes/config.yaml: even if auxiliary.sleep exists,
    default complete() must stay compression.
    """
    captured = {}

    def fake_call_llm(**kwargs):
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "Summary."}}]}

    fake_agent_module.call_llm = fake_call_llm

    adapter = _import_adapter()
    monkeypatch.setattr(
        adapter,
        "_load_hermes_config",
        lambda: {"auxiliary": {"sleep": {"provider": "xai-oauth", "model": "grok-4.6"}}},
    )
    backend = adapter.HermesAuxLLMBackend()
    out = backend.complete(
        "the prompt",
        max_tokens=128,
        temperature=0.2,
        timeout=12.0,
    )
    assert out == "Summary."
    assert captured["task"] == "compression"
    assert captured["temperature"] == 0.2
    assert captured["max_tokens"] == 128
    assert captured["timeout"] == 12.0
    # System prompt + user prompt structure.
    msgs = captured["messages"]
    assert msgs[0]["role"] == "system"
    assert "memory consolidation engine" in msgs[0]["content"].lower()
    # Slot-body rules belong on model_refresh, not every host-LLM complete().
    assert "400" not in msgs[0]["content"]
    assert "transcript" not in msgs[0]["content"].lower()
    assert msgs[1] == {"role": "user", "content": "the prompt"}
    # Optional overrides not passed when None.
    assert "provider" not in captured
    assert "model" not in captured


def test_complete_passes_provider_and_model_overrides(fake_agent_module):
    captured = {}
    fake_agent_module.call_llm = lambda **kw: (captured.update(kw) or {"choices": [{"message": {"content": "ok"}}]})

    adapter = _import_adapter()
    backend = adapter.HermesAuxLLMBackend()
    backend.complete(
        "x",
        max_tokens=64,
        temperature=0.0,
        timeout=10.0,
        provider="openai-codex",
        model="gpt-5.1-mini",
    )
    assert captured["provider"] == "openai-codex"
    assert captured["model"] == "gpt-5.1-mini"


def test_complete_returns_none_when_agent_import_unavailable(monkeypatch):
    """No fake agent in sys.modules → adapter returns None, never raises."""
    monkeypatch.delitem(sys.modules, "agent", raising=False)
    monkeypatch.delitem(sys.modules, "agent.auxiliary_client", raising=False)

    adapter = _import_adapter()
    backend = adapter.HermesAuxLLMBackend()
    assert backend.complete("x", max_tokens=64, temperature=0.0, timeout=5.0) is None


def test_complete_returns_none_when_call_llm_raises(fake_agent_module):
    def boom(**kwargs):
        raise RuntimeError("hermes is angry")

    fake_agent_module.call_llm = boom

    adapter = _import_adapter()
    backend = adapter.HermesAuxLLMBackend()
    assert backend.complete("x", max_tokens=64, temperature=0.0, timeout=5.0) is None


def test_complete_returns_none_when_response_has_no_content(fake_agent_module):
    fake_agent_module.call_llm = lambda **kw: {"choices": [{"message": {"content": ""}}]}
    adapter = _import_adapter()
    backend = adapter.HermesAuxLLMBackend()
    assert backend.complete("x", max_tokens=64, temperature=0.0, timeout=5.0) is None


def test_complete_uses_sleep_task_when_aux_sleep_model_configured(fake_agent_module, monkeypatch, caplog):
    captured = {}
    fake_agent_module.call_llm = lambda **kw: (captured.update(kw) or {"choices": [{"message": {"content": "ok"}}]})
    adapter = _import_adapter()
    monkeypatch.setattr(
        adapter,
        "_load_hermes_config",
        lambda: {"auxiliary": {"sleep": {"provider": "xai-oauth", "model": "grok-4.6"}}},
    )
    backend = adapter.HermesAuxLLMBackend()
    with caplog.at_level("INFO"):
        with adapter.sleep_aux_context():
            backend.complete("x", max_tokens=64, temperature=0.0, timeout=5.0)
    assert captured["task"] == "sleep"
    assert any("task=sleep" in rec.getMessage() and "model=grok-4.6" in rec.getMessage() for rec in caplog.records)


def test_complete_stays_compression_outside_sleep_context(fake_agent_module, monkeypatch):
    """extract_facts / model refresh must not inherit auxiliary.sleep."""
    captured = {}
    fake_agent_module.call_llm = lambda **kw: (captured.update(kw) or {"choices": [{"message": {"content": "ok"}}]})
    adapter = _import_adapter()
    monkeypatch.setattr(
        adapter,
        "_load_hermes_config",
        lambda: {"auxiliary": {"sleep": {"provider": "xai-oauth", "model": "grok-4.6"}}},
    )
    backend = adapter.HermesAuxLLMBackend()
    backend.complete("x", max_tokens=64, temperature=0.0, timeout=5.0)
    assert captured["task"] == "compression"
    assert adapter.is_sleep_aux_active() is False


# ---------------------------------------------------------------------------
# _extract_content() shape parsing
# ---------------------------------------------------------------------------

def test_extract_content_prefers_hermes_canonical_helper(fake_agent_module):
    """When Hermes' helper exists, use it (handles reasoning models)."""
    fake_agent_module.extract_content_or_reasoning = lambda resp: "from-helper"
    adapter = _import_adapter()
    out = adapter._extract_content({"choices": [{"message": {"content": "from-shape"}}]})
    assert out == "from-helper"


def test_extract_content_falls_back_when_helper_returns_empty(fake_agent_module):
    fake_agent_module.extract_content_or_reasoning = lambda resp: ""
    adapter = _import_adapter()
    out = adapter._extract_content({"choices": [{"message": {"content": "from-shape"}}]})
    assert out == "from-shape"


def test_extract_content_handles_object_response(fake_agent_module):
    fake_agent_module.extract_content_or_reasoning = lambda resp: ""

    class Msg:
        content = "object-content"

    class Choice:
        message = Msg()

    class Resp:
        choices = [Choice()]

    adapter = _import_adapter()
    assert adapter._extract_content(Resp()) == "object-content"


def test_extract_content_handles_dict_response(fake_agent_module):
    fake_agent_module.extract_content_or_reasoning = lambda resp: ""
    adapter = _import_adapter()
    out = adapter._extract_content({"choices": [{"message": {"content": "dict-content"}}]})
    assert out == "dict-content"


def test_extract_content_handles_object_with_content_attr(fake_agent_module):
    fake_agent_module.extract_content_or_reasoning = lambda resp: ""

    class Resp:
        content = "wrapped-content"

    adapter = _import_adapter()
    assert adapter._extract_content(Resp()) == "wrapped-content"


def test_extract_content_returns_none_for_unrecognized_shape(fake_agent_module):
    fake_agent_module.extract_content_or_reasoning = lambda resp: ""
    adapter = _import_adapter()
    assert adapter._extract_content(object()) is None
    assert adapter._extract_content(None) is None
    assert adapter._extract_content({"unrelated": "shape"}) is None


# ---------------------------------------------------------------------------
# register/unregister
# ---------------------------------------------------------------------------

def test_register_hermes_host_llm_installs_backend(monkeypatch):
    adapter = _import_adapter()
    assert get_host_llm_backend() is None
    assert adapter.register_hermes_host_llm() is True
    backend = get_host_llm_backend()
    assert backend is not None
    assert backend.name == "hermes"
    # Cleanup happens via autouse fixture, but be explicit.
    adapter.unregister_hermes_host_llm()


def test_unregister_clears_backend():
    adapter = _import_adapter()
    adapter.register_hermes_host_llm()
    assert get_host_llm_backend() is not None
    adapter.unregister_hermes_host_llm()
    assert get_host_llm_backend() is None


def test_register_returns_false_when_mnemosyne_registry_missing(monkeypatch):
    """If the registry import fails, register returns False instead of raising."""
    adapter = _import_adapter()
    # Sabotage the import path used inside register_hermes_host_llm.
    monkeypatch.setitem(sys.modules, "mnemosyne.core.llm_backends", None)
    try:
        assert adapter.register_hermes_host_llm() is False
    finally:
        # Restore the real module so later tests see a working registry.
        monkeypatch.undo()
