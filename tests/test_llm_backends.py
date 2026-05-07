"""Unit tests for the host LLM backend registry."""

from __future__ import annotations

import pytest

from mnemosyne.core import llm_backends
from mnemosyne.core.llm_backends import (
    CallableLLMBackend,
    call_host_llm,
    get_host_llm_backend,
    set_host_llm_backend,
)


def test_set_get_backend_round_trip():
    assert get_host_llm_backend() is None

    backend = CallableLLMBackend("test", lambda *a, **k: "ok")
    set_host_llm_backend(backend)
    try:
        assert get_host_llm_backend() is backend
    finally:
        set_host_llm_backend(None)

    assert get_host_llm_backend() is None


def test_call_host_llm_returns_none_without_backend():
    assert get_host_llm_backend() is None
    assert call_host_llm("anything", max_tokens=64) is None


def test_call_host_llm_passes_args_through():
    captured = {}

    def fake(prompt, *, max_tokens, temperature, timeout, provider=None, model=None):
        captured["prompt"] = prompt
        captured["max_tokens"] = max_tokens
        captured["temperature"] = temperature
        captured["timeout"] = timeout
        captured["provider"] = provider
        captured["model"] = model
        return "out"

    set_host_llm_backend(CallableLLMBackend("test", fake))
    try:
        result = call_host_llm(
            "hello",
            max_tokens=128,
            temperature=0.1,
            timeout=7.5,
            provider="openai-codex",
            model="gpt-5.1-mini",
        )
    finally:
        set_host_llm_backend(None)

    assert result == "out"
    assert captured == {
        "prompt": "hello",
        "max_tokens": 128,
        "temperature": 0.1,
        "timeout": 7.5,
        "provider": "openai-codex",
        "model": "gpt-5.1-mini",
    }


def test_call_host_llm_swallows_exception_returns_none():
    def boom(*a, **k):
        raise RuntimeError("provider exploded")

    set_host_llm_backend(CallableLLMBackend("test", boom))
    try:
        assert call_host_llm("anything", max_tokens=64) is None
    finally:
        set_host_llm_backend(None)


def test_callable_llm_backend_dispatches_to_func():
    seen = []

    def fake(prompt, *, max_tokens, temperature, timeout, provider=None, model=None):
        seen.append(prompt)
        return prompt.upper()

    backend = CallableLLMBackend("upper", fake)
    out = backend.complete(
        "hi",
        max_tokens=32,
        temperature=0.0,
        timeout=5.0,
    )
    assert out == "HI"
    assert seen == ["hi"]


def test_set_none_unregisters_backend():
    set_host_llm_backend(CallableLLMBackend("test", lambda *a, **k: "x"))
    assert get_host_llm_backend() is not None
    set_host_llm_backend(None)
    assert get_host_llm_backend() is None
