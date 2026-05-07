"""
Mnemosyne Host LLM Backend Registry
===================================
Pluggable adapter for routing Mnemosyne's LLM-backed memory operations
through a host-provided completion endpoint (e.g., Hermes' authenticated
auxiliary client).

Standalone Mnemosyne ignores this registry. When a host registers a backend
and the user opts in via MNEMOSYNE_HOST_LLM_ENABLED=true, both consolidation
(summarize_memories) and structured fact extraction (extract_facts) consult
the backend before falling through to the existing remote/local chain.

The interface is intentionally tiny: one method, prompt-shaped, returning
text-or-None. The caller owns the system prompt and content per task; the
backend just routes to whichever provider/model the host has authenticated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol


class LLMBackend(Protocol):
    """A host-provided LLM completion endpoint.

    Implementations route a single prompt string through the host's
    authenticated provider and return the cleaned text, or None on failure.

    The method is named ``complete`` (not ``summarize``) because the same
    backend serves both memory consolidation and structured fact extraction;
    the caller, not the backend, owns the system prompt.
    """

    name: str

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        timeout: float,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Optional[str]:
        ...


@dataclass
class CallableLLMBackend:
    """Wrap a callable as an :class:`LLMBackend`. Useful for tests and one-off callers."""

    name: str
    func: Callable[..., Optional[str]]

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        timeout: float,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Optional[str]:
        return self.func(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            provider=provider,
            model=model,
        )


_backend: Optional[LLMBackend] = None


def set_host_llm_backend(backend: Optional[LLMBackend]) -> None:
    """Register (or clear) the process-global host LLM backend.

    Hosts call this from their initialize/shutdown hooks. Pass ``None``
    to unregister.
    """
    global _backend
    _backend = backend


def get_host_llm_backend() -> Optional[LLMBackend]:
    """Return the registered host LLM backend, or None."""
    return _backend


def call_host_llm(
    prompt: str,
    *,
    max_tokens: int,
    temperature: float = 0.3,
    timeout: float = 15.0,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[str]:
    """Convenience wrapper: call the registered backend if any, swallow failures.

    Returns ``None`` when no backend is registered or the backend raises.
    Logging is intentionally minimal here; callers that need provenance
    should log around the call site (and never log the prompt itself).
    """
    backend = get_host_llm_backend()
    if backend is None:
        return None
    try:
        return backend.complete(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            provider=provider,
            model=model,
        )
    except Exception:
        return None
