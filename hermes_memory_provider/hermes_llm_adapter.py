"""
Hermes Auxiliary LLM Adapter
============================
Bridges Mnemosyne's host-LLM registry (``mnemosyne.core.llm_backends``) and
Hermes' authenticated auxiliary client (``agent.auxiliary_client.call_llm``).

Why this lives in ``hermes_memory_provider`` and not ``mnemosyne.core``:

- Mnemosyne core must remain Hermes-free. ``agent.*`` is imported only inside
  the call path of this adapter, never at module import time.
- The adapter is registered when the Hermes memory provider initializes and
  unregistered on shutdown, leaving standalone Mnemosyne use untouched.

Behavior:

- ``HermesAuxLLMBackend.complete()`` is the host-LLM entry point. It calls
  ``call_llm(task="compression", ...)`` so Hermes handles auth, OAuth refresh,
  Codex Responses API translation, and provider fallback.
- ``register_hermes_host_llm()`` installs the backend in the registry.
- ``unregister_hermes_host_llm()`` removes it (called from
  ``MnemosyneMemoryProvider.shutdown()`` so a process that later runs Mnemosyne
  outside Hermes does not retain a stale Hermes reference).

Failure mode: any failure (Hermes import error, ``call_llm`` exception, no
extractable content) returns ``None``. The Mnemosyne caller treats that as
"host attempted, no usable text" and falls through to the local GGUF path
(never to ``MNEMOSYNE_LLM_BASE_URL`` — see decision A3 in the plan).
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class HermesAuxLLMBackend:
    """LLMBackend implementation that routes through Hermes' aux client.

    The ``task`` attribute pins the Hermes auxiliary slot used for memory ops.
    ``compression`` is the closest existing fit; introducing a Hermes-side
    ``memory`` task is left as a follow-up.
    """

    name = "hermes"
    task = "compression"

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
        try:
            from agent.auxiliary_client import call_llm
        except Exception as exc:
            logger.debug("Hermes aux LLM unavailable: %s", exc)
            return None

        kwargs = {
            "task": self.task,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a memory consolidation engine. Follow the user prompt exactly. "
                        "Preserve durable facts, names, preferences, decisions, and chronology. "
                        "Do not add facts not present in the input."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": timeout,
        }
        # Optional non-secret overrides — only include when set, so Hermes' own
        # auxiliary.compression resolution remains the default.
        if provider:
            kwargs["provider"] = provider
        if model:
            kwargs["model"] = model

        try:
            response = call_llm(**kwargs)
        except Exception as exc:
            logger.warning("Hermes aux LLM call failed; falling back: %s", exc)
            return None

        return _extract_content(response)


def _extract_content(response) -> Optional[str]:
    """Extract usable text from a Hermes response, handling reasoning models.

    Prefers Hermes' canonical helper (``extract_content_or_reasoning``) when
    available — it correctly handles providers like Codex/o1-style reasoning
    models where ``message.content`` may be empty but ``reasoning`` carries
    the real output. Falls back to ad-hoc shape matching for older Hermes
    builds that lack the helper.
    """
    # 1. Hermes' canonical parser (correct for reasoning models).
    try:
        from agent.auxiliary_client import extract_content_or_reasoning  # type: ignore
        text = extract_content_or_reasoning(response)
        if isinstance(text, str) and text.strip():
            return text.strip()
    except Exception:
        # Helper not available or threw — fall through to defensive parsing.
        pass

    # 2. OpenAI-style object response.
    try:
        content = response.choices[0].message.content
        if isinstance(content, str) and content.strip():
            return content.strip()
    except Exception:
        pass

    # 3. Dict-shaped response (test mocks, some normalized wrappers).
    if isinstance(response, dict):
        try:
            content = response["choices"][0]["message"]["content"]
            if isinstance(content, str) and content.strip():
                return content.strip()
        except Exception:
            pass

    # 4. Object exposing ``.content`` directly (some Hermes wrappers).
    content = getattr(response, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()

    return None


def register_hermes_host_llm() -> bool:
    """Install :class:`HermesAuxLLMBackend` in the Mnemosyne host-LLM registry.

    Returns True on success, False if the Mnemosyne registry is unavailable.
    Registration alone does not change Mnemosyne behavior — the user still
    has to set ``MNEMOSYNE_HOST_LLM_ENABLED=true``.
    """
    try:
        from mnemosyne.core.llm_backends import set_host_llm_backend
        set_host_llm_backend(HermesAuxLLMBackend())
        return True
    except Exception as exc:
        logger.debug("Failed to register Hermes host LLM backend: %s", exc)
        return False


def unregister_hermes_host_llm() -> None:
    """Symmetric unregistration for shutdown(). Never raises."""
    try:
        from mnemosyne.core.llm_backends import set_host_llm_backend
        set_host_llm_backend(None)
    except Exception as exc:
        logger.debug("Failed to unregister Hermes host LLM backend: %s", exc)
