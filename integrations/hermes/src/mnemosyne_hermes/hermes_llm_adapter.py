"""
Hermes Auxiliary LLM Adapter
============================
Bridges Mnemosyne's host-LLM registry (``mnemosyne.core.llm_backends``) and
Hermes' authenticated auxiliary client (``agent.auxiliary_client.call_llm``).

Why this lives in ``mnemosyne_hermes`` and not in ``mnemosyne.core``:

- Mnemosyne core must remain Hermes-free. ``agent.*`` is imported only inside
  the call path of this adapter, never at module import time.
- The adapter is registered when the Hermes memory provider initializes and
  unregistered on shutdown, leaving standalone Mnemosyne use untouched.

Behavior:

- ``HermesAuxLLMBackend.complete()`` is the host-LLM entry point. Default
  ``complete()`` stays ``task=compression`` so ``extract_facts`` / model
  refresh do not inherit the idle sleep model. Sleep / consolidation
  callers wrap work in ``sleep_aux_context()``; only then does
  ``complete()`` resolve ``auxiliary.sleep`` (when that slot has a
  provider or model) else ``auxiliary.compression``. Blindly passing
  task=sleep without a configured slot would fall through to the main model.
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

import contextvars
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

SLEEP_AUX_TASK = "sleep"
COMPRESSION_AUX_TASK = "compression"

_SLEEP_AUX_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "mnemosyne_sleep_aux_active", default=False
)


def is_sleep_aux_active() -> bool:
    """True while a sleep / consolidation caller has entered sleep_aux_context()."""
    return bool(_SLEEP_AUX_ACTIVE.get())


@contextmanager
def sleep_aux_context() -> Iterator[None]:
    """Mark host-LLM calls in this context as sleep / consolidation.

    Set this around beam.sleep / CLI sleep / auto-sleep / session-end so
    ``complete()`` may resolve ``auxiliary.sleep``. Default ``complete()``
    stays compression. Contextvars are not copied into new threads — enter
    this manager *inside* the worker thread that calls ``beam.sleep()``.
    """
    token = _SLEEP_AUX_ACTIVE.set(True)
    try:
        yield
    finally:
        _SLEEP_AUX_ACTIVE.reset(token)


@dataclass(frozen=True)
class SleepAuxResolution:
    """Resolved Hermes auxiliary slot for sleep / consolidation."""

    task: str
    model: Optional[str] = None
    provider: Optional[str] = None

    def log_message(self) -> str:
        return (
            f"Hermes memory aux resolved task={self.task} "
            f"model={self.model or '(unset)'} provider={self.provider or '(unset)'}"
        )

    def cli_line(self) -> str:
        return (
            f"sleep aux: task={self.task} "
            f"model={self.model or '(unset)'} provider={self.provider or '(unset)'}"
        )


def _nonempty_str(value: Any) -> Optional[str]:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _mapping_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None or isinstance(obj, (str, bytes)):
        return default
    getter = getattr(obj, "get", None)
    if not callable(getter):
        return default
    try:
        value = getter(key, default)
    except TypeError:
        try:
            value = getter(key)
        except Exception:
            return default
    except Exception:
        return default
    return default if value is None else value


def _aux_slot(config: Any, name: str) -> Any:
    auxiliary = _mapping_get(config, "auxiliary", {})
    return _mapping_get(auxiliary, name, {})


def _slot_provider_model(slot: Any) -> tuple[Optional[str], Optional[str]]:
    return (
        _nonempty_str(_mapping_get(slot, "provider")),
        _nonempty_str(_mapping_get(slot, "model")),
    )


def _load_hermes_config() -> Any:
    try:
        from hermes_cli.config import load_config
        return load_config()
    except Exception:
        return {}


def resolve_sleep_aux_task(config: Any = None) -> SleepAuxResolution:
    """Return ``sleep`` only when ``auxiliary.sleep`` has provider or model.

    Missing config, unreadable config, or a sleep slot with neither provider
    nor model (timeout-only / empty strings) falls back to ``compression``.
    Never raises — a missing sleep slot must not crash the sleep worker.
    """
    try:
        if config is None:
            config = _load_hermes_config()
        provider, model = _slot_provider_model(_aux_slot(config, SLEEP_AUX_TASK))
        if provider or model:
            return SleepAuxResolution(task=SLEEP_AUX_TASK, model=model, provider=provider)
        c_provider, c_model = _slot_provider_model(_aux_slot(config, COMPRESSION_AUX_TASK))
        return SleepAuxResolution(
            task=COMPRESSION_AUX_TASK, model=c_model, provider=c_provider
        )
    except Exception:
        return SleepAuxResolution(task=COMPRESSION_AUX_TASK)


def format_sleep_aux_resolution(resolved: Optional[SleepAuxResolution] = None) -> str:
    """One-line CLI description of the resolved sleep aux slot."""
    if resolved is None:
        resolved = resolve_sleep_aux_task()
    return resolved.cli_line()


def _consolidation_system_prompt() -> str:
    """Generic consolidation system prompt; slot-body rules stay off complete()."""
    try:
        from mnemosyne.core.model_refresh import consolidation_system_prompt
    except Exception:
        return (
            "You are a memory consolidation engine. Follow the user prompt exactly. "
            "Preserve durable facts, names, preferences, decisions, and chronology. "
            "Do not add facts not present in the input. "
            "Never write or update SOUL.md."
        )
    return consolidation_system_prompt()


class HermesAuxLLMBackend:
    """LLMBackend implementation that routes through Hermes' aux client.

    Default ``complete()`` uses ``auxiliary.compression``. Sleep /
    consolidation callers wrap ``beam.sleep`` in ``sleep_aux_context()``
    so ``complete()`` may resolve ``auxiliary.sleep`` when that slot is
    configured. ``self.task`` remains the compression fallback name.
    """

    name = "hermes"
    task = COMPRESSION_AUX_TASK

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

        if is_sleep_aux_active():
            resolved = resolve_sleep_aux_task()
            logger.info(resolved.log_message())
            task = resolved.task or self.task
        else:
            task = self.task

        kwargs = {
            "task": task,
            "messages": [
                {
                    "role": "system",
                    "content": _consolidation_system_prompt(),
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
