# Hermes Auxiliary LLM Adapter for Mnemosyne Implementation Plan

> **For Hermes:** Use `subagent-driven-development` only if Chris asks to implement this later. This document is a design/implementation plan, not authorization to change upstream behavior yet.

**Goal:** Allow Mnemosyne, when running inside Hermes, to use Hermes' configured main/auxiliary LLM routing for memory consolidation and structured fact extraction, including OAuth-backed providers such as `openai-codex`, without hardcoding Hermes auth into Mnemosyne core.

**Architecture:** Add a small host-LLM adapter interface in Mnemosyne core and a Hermes-specific adapter in the Hermes plugin/provider package. The core keeps its current remote/local/fallback behavior, but gains an optional injected backend that is consulted before the existing OpenAI-compatible/local paths whenever it is registered and explicitly enabled. Hermes integration calls `agent.auxiliary_client.call_llm(task="compression", ...)`, so it inherits Hermes model config, auxiliary compression slot, provider auth, OAuth refresh, Codex Responses API translation, and future provider-registry improvements.

**Tech Stack:** Python, pytest, Mnemosyne core package, Hermes plugin/provider package, Hermes `agent.auxiliary_client` public-ish helper. No new runtime dependency required.

---

## Plan revision history

- **2026-05-07 — Initial draft** (designed with Hermes in conversation; covers `summarize_memories()` only).
- **2026-05-07 — Eng review revision** (this document). Walked through `/plan-eng-review` with an outside Codex review. 14 decisions captured (A1-A6 architecture, C1-C8 quality). Material changes: extension to `extract_facts()`, sentinel-tuple return for the helper, `llm_available()` fix, host-friendly prompt builder, host-aware chunking, Hermes response-parser reuse, lifecycle cleanup, bounded `on_session_end()`, renumbered duplicate Task 5. See `Decision log` near the end of this document for the full list with rationale.

---

## Current source verification

Checked on 2026-05-07:

```bash
git -C /git_projects/mnemosyne fetch origin
git -C /git_projects/mnemosyne status --short --branch
git -C /git_projects/mnemosyne log -1 --oneline HEAD
git -C /git_projects/mnemosyne log -1 --oneline origin/main
```

Result:

```text
## main...origin/main
0b2d9f1 fix: resolve issues #24, #25, #26, #27
0b2d9f1 fix: resolve issues #24, #25, #26, #27
```

So `/git_projects/mnemosyne` is current with GitHub `origin/main` as of this plan.

---

## Executor handoff for future agents with no chat context

Follow these constraints exactly unless Chris explicitly changes them:

1. Treat this file as the authoritative brief. Do not infer requirements from prior chat transcripts.
2. Source of truth for Mnemosyne is `/git_projects/mnemosyne`; source of truth for Hermes behavior is the installed/fetched Hermes checkout at `~/.hermes/hermes-agent` plus upstream docs/code.
3. Do **not** read, print, copy, store, or log Hermes OAuth tokens, API keys, refresh tokens, `auth.json` contents, or `.env` values. If a test requires credentials, mock the adapter instead.
4. Do **not** point Mnemosyne directly at `https://chatgpt.com/backend-api/codex` through `MNEMOSYNE_LLM_BASE_URL`; that endpoint is not an OpenAI-compatible API-key endpoint and must remain behind Hermes' auth/provider layer.
5. No new packages, plugins, installers, cron jobs, auth flows, or network services without explicit approval.
6. Keep this surgical: avoid moving storage paths, rewriting BEAM, changing the installer, or restructuring Mnemosyne around Hermes.
7. Preserve non-Hermes use: standalone Mnemosyne must still work with the current env-var OpenAI-compatible remote path, local GGUF path, and non-LLM fallback.
8. Tests must be mock-only for Hermes LLM calls. No live LLM calls in CI. When patching `agent.auxiliary_client.call_llm`, inject a fake `agent` package into `sys.modules` first (Hermes is not a test-time dependency); otherwise the import path itself fails before the adapter behavior is exercised.
9. Fail open: if Hermes LLM resolution, auth, import, timeout, or response parsing fails, Mnemosyne should fall back per the precedence rules below and should not block a Hermes turn or Hermes session shutdown indefinitely.
10. Keep logs useful but non-secret-bearing: log backend name/provider/model if safe, never token/key/header values.

---

## Why this is needed

Current Mnemosyne LLM support is standalone-oriented:

- `mnemosyne/core/local_llm.py` checks `MNEMOSYNE_LLM_BASE_URL`, `MNEMOSYNE_LLM_API_KEY`, and `MNEMOSYNE_LLM_MODEL`.
- Remote calls are hardcoded to `{base_url}/chat/completions` with an optional Authorization header when `MNEMOSYNE_LLM_API_KEY` is supplied.
- Local fallback tries `llama-cpp-python` and `ctransformers`.
- If those fail, consolidation returns `None` and callers fall back to non-LLM behavior.
- The same `local_llm.py` infrastructure is used by `mnemosyne/core/extraction.py:extract_facts()` (called from `_extract_and_store_facts()` in `beam.py:535-561` whenever `remember(extract=True)` is used). So fact extraction shares the constraint with consolidation.

That cannot use Hermes' OpenAI Max/Pro subscription path because Hermes' `openai-codex` support is OAuth/session-backed and uses provider-specific routing, headers, credential refresh, and Responses API translation. Mnemosyne should call Hermes' already-authenticated LLM capability instead of copying or reimplementing that auth — and it should do so for **both consolidation and fact extraction** (see decision A1).

---

## Relevant Hermes design inspiration

Hermes recently moved toward modular provider metadata instead of requiring every provider/model update to be encoded as core logic:

1. Current local Hermes has `hermes_cli/providers.py`, which merges:
   - a live/bundled `models.dev` catalog,
   - small Hermes-specific overlays,
   - user-configured providers.
2. Hermes upstream commit `20a4f79ed feat: provider modules — ProviderProfile ABC, 33 providers, fetch_models, transport single-path` shows the intended direction even more clearly:
   - `providers/base.py` defines a declarative `ProviderProfile`.
   - `providers/__init__.py` auto-discovers provider modules via `pkgutil.iter_modules()`.
   - provider modules such as `providers/openai_codex.py` declare auth type, base URL, API mode, aliases, default aux model, and quirks.
   - transports consume provider profiles instead of scattered conditionals.
3. Hermes `agent/auxiliary_client.py` is the useful integration point today:
   - `call_llm(task="compression", messages=[...], max_tokens=..., timeout=...)`
   - `_resolve_task_provider_model()` reads `auxiliary.<task>.provider/model/base_url/api_key/api_mode`.
   - `openai-codex` is used when selected as the main provider or explicitly configured with a model; it is intentionally not guessed as a fallback because Codex model allow-lists drift.
   - `_build_codex_client()` resolves OAuth-backed Codex credentials and wraps the Responses API behind a Chat Completions-like surface.
   - Hermes also exposes a canonical response parser (`extract_content_or_reasoning()` on the auxiliary client) that handles reasoning-model responses where `message.content` may be empty but `reasoning` blocks carry the real output. The Hermes adapter in this plan uses that helper rather than rebuilding it (see C5).

Mnemosyne should copy the **shape of the design**, not Hermes internals: small capability interface, adapter modules, dynamic/optional registration, and explicit task routing.

---

## Proposed behavior

### Config precedence

For LLM-backed memory operations using the shared `local_llm.py` infrastructure (consolidation via `summarize_memories()` and structured fact extraction via `extract_facts()`):

1. **`MNEMOSYNE_LLM_ENABLED=false`** disables LLM-backed consolidation and fact extraction, including host adapters. Today this gate is enforced by `_load_llm()` (local path) but **does not** currently block the remote `LLM_BASE_URL` path — `llm_available()` returns True whenever `LLM_BASE_URL` is set, before checking `LLM_ENABLED`. This plan closes that gap so `LLM_ENABLED=false` honestly disables every LLM-backed memory operation, including remote, local, and host (see decision A2).
2. **`MNEMOSYNE_HOST_LLM_ENABLED=true` AND a host backend is registered:**
   - Use the host backend.
   - **If the host attempt produces no usable text (errors, returns empty, no extractable content), skip `MNEMOSYNE_LLM_BASE_URL` entirely** and fall through to local GGUF, then to None. The remote path is not consulted when host is enabled — even on failure (see decision A3). This prevents accidentally routing memory content to a stale `MNEMOSYNE_LLM_BASE_URL` the user forgot to clear.
   - **If `MNEMOSYNE_HOST_LLM_ENABLED=true` but no backend is registered**, treat the host as "not attempted" and proceed with the existing remote/local fallback chain. This means a user who sets the env var but loads Mnemosyne outside Hermes does not lose their existing setup.
   - Optional `MNEMOSYNE_HOST_LLM_PROVIDER` and `MNEMOSYNE_HOST_LLM_MODEL` are passed to the host backend only when non-empty. These override the host's default compression model for Mnemosyne calls without requiring Mnemosyne to manage credentials.
   - Optional `MNEMOSYNE_HOST_LLM_N_CTX` (default `32000`) governs prompt-budget chunking when the host backend is the chosen path. The existing TinyLlama-calibrated `LLM_N_CTX=2048` is unsuitable for Codex/GPT-class context windows (see decision C6).
3. Otherwise, preserve the existing Mnemosyne behavior exactly:
   - Existing explicit Mnemosyne remote API config, if present: `MNEMOSYNE_LLM_BASE_URL` + optional `MNEMOSYNE_LLM_API_KEY` + `MNEMOSYNE_LLM_MODEL`.
   - Existing local GGUF path: `llama-cpp-python`, then `ctransformers`.
   - Existing non-LLM fallback: return `None` (consolidation) or `[]` (extraction); caller uses current AAAK/non-semantic behavior.

Rationale: do not change the existing `MNEMOSYNE_LLM_ENABLED` default or existing remote/local/fallback precedence. Hermes/host use must be explicitly requested with `MNEMOSYNE_HOST_LLM_ENABLED=true`; otherwise standalone and existing configured users see no behavior change after upgrading.

The "host attempted vs not attempted" distinction is implemented via a sentinel-tuple return on the helper (see C1). An `Optional[str]` return alone cannot express "no backend registered" vs "backend was called and returned nothing", which is required to honor A3.

### Hermes-specific default

When Mnemosyne is loaded by Hermes as a memory provider/plugin, register the Hermes auxiliary LLM backend as an available host backend during `MnemosyneMemoryProvider.initialize()`. Registration alone must not change behavior. Mnemosyne uses the registered host backend only when `MNEMOSYNE_HOST_LLM_ENABLED=true` and `MNEMOSYNE_LLM_ENABLED` has not disabled LLM-backed memory operations.

`MnemosyneMemoryProvider.shutdown()` must symmetrically unregister the host backend (see C7) so a process that uses Mnemosyne again outside the Hermes session does not retain a stale reference to `agent.auxiliary_client`.

Do not require users to set Codex-specific base URLs, tokens, or Mnemosyne-specific API keys for the Hermes path. Users normally choose their model in Hermes itself. If they need Mnemosyne to use a different host model than Hermes' own `auxiliary.compression` default, allow optional non-secret per-call overrides via `MNEMOSYNE_HOST_LLM_PROVIDER` and `MNEMOSYNE_HOST_LLM_MODEL`.

### Hermes auxiliary task

Use Hermes task `compression` as the hardcoded adapter default for now:

```python
call_llm(
    task="compression",
    messages=[
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
    temperature=temperature,   # 0.0 for fact extraction, 0.3 for summarization (see C2)
    max_tokens=LLM_MAX_TOKENS,
    timeout=HOST_LLM_TIMEOUT,
)
```

`task="compression"` is used for Hermes routing only: provider/model/timeout/auth/fallback selection. Mnemosyne still owns the natural-language request by supplying both the system message and user prompt.

This means users can configure:

```yaml
auxiliary:
  compression:
    provider: openai-codex   # or auto/main/openrouter/etc.
    model: gpt-5.4-mini      # required when forcing openai-codex if main model not inherited
    timeout: 15
```

If Hermes `auxiliary.compression.*` is unset, Hermes' auxiliary client currently treats `auto` as main provider + main model first, then fallback chain. That is acceptable and future-proof because it follows Hermes' own provider evolution.

**Timeout caveat:** the `timeout` argument to `call_llm()` is per-attempt. Hermes can internally retry for unsupported parameters, auth refresh, payment fallback, and connection fallback; total wall-clock can exceed `HOST_LLM_TIMEOUT`. The 10–15 second guidance in this plan should be read as a per-attempt budget; budget for 2-3× on cold start.

---

## Non-goals

Do not include these in the surgical PR:

- Do not rewrite BEAM, storage paths, embeddings, triples, or the installer.
- Do not add a direct ChatGPT/Codex client to Mnemosyne core.
- Do not parse Hermes `auth.json` in Mnemosyne.
- Do not add a Mnemosyne-owned provider catalog.
- Do not make Hermes a required dependency of `mnemosyne.core`.
- Do not modify `mnemosyne/extraction/client.py` (the OpenRouter `ExtractionClient` used only by `tools/evaluate_beam_end_to_end.py` with `--use-cloud`). It is gated by `--use-cloud` and uses `OPENROUTER_API_KEY` independently of `local_llm.py`. Routing it through the host backend is a separate change (see decision A4).
- Do not fix the pre-existing inconsistency in `mnemosyne/core/extraction.py` where the local fallback at line 90 calls `llm(prompt, ...)` directly (ctransformers shape) instead of routing through `_call_local_llm()` (llama-cpp chat shape). Out of scope; flagged for a follow-up.
- Do not introduce plugin auto-discovery / entry-points for host backends — defer until there is a second host integration.
- Do not change `MNEMOSYNE_LLM_ENABLED`'s default. Honor it consistently across host/remote/local (see A2).

The original draft included "Do not run sleep/consolidation synchronously inside the Hermes TUI/gateway without timeout/fail-open safeguards." That non-goal stands, and this plan now actively enforces it on the `on_session_end()` path that previously violated it (see decision A6).

---

## Implementation plan

### Task 1 — Add a tiny host LLM adapter interface in core

**Objective:** Give Mnemosyne core a dependency-free way to call an optional host-provided LLM.

**Files:**
- Create: `mnemosyne/core/llm_backends.py`
- Test: `tests/test_llm_backends.py`

**Design:**

```python
# mnemosyne/core/llm_backends.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol


class LLMBackend(Protocol):
    """A pluggable LLM completion endpoint.

    Implementations route a single prompt string through the host's
    authenticated provider (e.g., Hermes' auxiliary client) and return
    the cleaned text or None on failure.

    The method is named `complete` (not `summarize`) because the same
    backend serves both memory consolidation and structured fact
    extraction; the caller, not the backend, owns the system prompt.
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
    """Adapter for tests and one-off callers; wraps a function as an LLMBackend."""
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
    global _backend
    _backend = backend


def get_host_llm_backend() -> Optional[LLMBackend]:
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
        # Logging can be added, but do not leak prompts or secrets.
        return None
```

**Test cases:**

- `test_set_get_backend_round_trip` — set, get returns same instance, set None clears.
- `test_call_host_llm_returns_none_without_backend`
- `test_call_host_llm_passes_args_through` — assert `backend.complete` received exact prompt, max_tokens, temperature, timeout, provider, model.
- `test_call_host_llm_swallows_exception_returns_none`
- `test_callable_llm_backend_dispatches_to_func`

**Verification:**

```bash
python -m pytest tests/test_llm_backends.py -q
```

Expected: all tests pass.

---

### Task 2 — Teach `local_llm.py` and `extraction.py` to try host backend, behind explicit gates

**Objective:** Insert one optional host-backend call into both LLM-using call sites (consolidation and fact extraction), without changing existing behavior unless `MNEMOSYNE_HOST_LLM_ENABLED=true` is set.

**Files:**
- Modify: `mnemosyne/core/local_llm.py`
- Modify: `mnemosyne/core/extraction.py`
- Test: `tests/test_local_llm.py`
- Test: `tests/test_extraction.py` (new)

**Module constants (add to `local_llm.py` near the existing LLM constants):**

```python
HOST_LLM_ENABLED = os.environ.get("MNEMOSYNE_HOST_LLM_ENABLED", "false").lower() in ("1", "true", "yes")
HOST_LLM_PROVIDER = os.environ.get("MNEMOSYNE_HOST_LLM_PROVIDER", "").strip() or None
HOST_LLM_MODEL = os.environ.get("MNEMOSYNE_HOST_LLM_MODEL", "").strip() or None
HOST_LLM_TIMEOUT = 15.0  # Per-attempt safety cap; not user-facing.
HOST_LLM_N_CTX = int(os.environ.get("MNEMOSYNE_HOST_LLM_N_CTX", "32000"))  # see C6
```

`MNEMOSYNE_HOST_LLM_PROVIDER` and `MNEMOSYNE_HOST_LLM_MODEL` are optional. They override the host default compression provider/model for Mnemosyne calls only. Leave them unset to let Hermes resolve through its normal `auxiliary.compression` / main-model / fallback behavior. `MNEMOSYNE_HOST_LLM_N_CTX` controls the chunking budget when host is the chosen path.

**Add helper in `local_llm.py` — single source of truth for host attempts (decision C1):**

```python
def _try_host_llm(
    prompt: str,
    *,
    max_tokens: int,
    temperature: float,
) -> tuple[bool, Optional[str]]:
    """Attempt the host LLM backend if enabled.

    Returns (attempted, text):
      - (False, None) when LLM_ENABLED or HOST_LLM_ENABLED is false, OR no backend is registered.
      - (True, text-or-None) when the backend was called.

    The 'attempted' flag is the sentinel callers use to honor decision A3:
    when host is enabled and attempted=True, the existing MNEMOSYNE_LLM_BASE_URL
    path MUST be skipped on failure (host went straight to local/None).
    """
    if not LLM_ENABLED or not HOST_LLM_ENABLED:
        return (False, None)
    from mnemosyne.core.llm_backends import call_host_llm, get_host_llm_backend
    if get_host_llm_backend() is None:
        return (False, None)
    raw = call_host_llm(
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=HOST_LLM_TIMEOUT,
        provider=HOST_LLM_PROVIDER,
        model=HOST_LLM_MODEL,
    )
    cleaned = _clean_output(raw) if raw else None
    return (True, cleaned if cleaned else None)
```

**Add a host-friendly prompt builder (decision C4):**

`_build_prompt()` (`local_llm.py:193-204`) emits TinyLlama chat-template tokens (`<|user|>`, `</s>`, `<|assistant|>`). Sending those to Codex as the `user` content of a Chat Completions message produces degraded output. Add a sibling builder for the host path:

```python
def _build_host_prompt(memories: List[str], source: str = "") -> str:
    """Plain-text prompt for host LLMs (no TinyLlama chat-template tokens)."""
    # Identical content shape to _build_prompt() but without <|user|>/</s>/<|assistant|>.
    # Implementation details left to the executor — mirror _build_prompt() and strip the templating.
```

Verify in implementation whether `_build_extraction_prompt()` shares the same TinyLlama tokens; if so, add `_build_host_extraction_prompt()` analogously. Existing local/remote callers continue using the existing builders.

**Modify `summarize_memories()` (`local_llm.py:333-360`):**

After `prompt = _build_prompt(memories, source=source)` and before `if LLM_BASE_URL:`, insert:

```python
    # Optional host-provided LLM adapter (Hermes or another agent).
    # Disabled by default. When enabled, MNEMOSYNE_LLM_BASE_URL is NOT consulted
    # on host failure (see decision A3) — falls straight to local GGUF then None.
    host_prompt = _build_host_prompt(memories, source=source)
    attempted, text = _try_host_llm(host_prompt, max_tokens=LLM_MAX_TOKENS, temperature=0.3)
    if attempted:
        if text:
            return text
        # Host attempted but returned nothing. Skip remote; fall to local GGUF.
        local_text = _call_local_llm(prompt)
        return _clean_output(local_text) if local_text else None
```

When `attempted=False` (host disabled or no backend), the function continues with the existing `if LLM_BASE_URL:` → local → None chain unchanged.

**Modify `extract_facts()` (`mnemosyne/core/extraction.py:62-99`) — decision A1:**

Mirror the same pattern in `extract_facts()`, with `temperature=0.0` for determinism (decision C2):

```python
def extract_facts(text: str) -> List[str]:
    if not text or not text.strip():
        return []
    if not llm_available():
        return []

    prompt = _build_extraction_prompt(text)

    # --- Try host backend first if enabled ---
    from mnemosyne.core.local_llm import _try_host_llm, _build_host_extraction_prompt
    host_prompt = _build_host_extraction_prompt(text)  # or reuse _build_extraction_prompt if no TinyLlama tokens
    attempted, host_text = _try_host_llm(host_prompt, max_tokens=LLM_MAX_TOKENS, temperature=0.0)
    if attempted:
        if host_text:
            facts = _parse_facts(host_text)
            if facts:
                return facts
        # Host attempted but produced no facts. Skip remote; fall to local GGUF.
        llm = _load_llm()
        if llm is not None:
            try:
                raw_output = llm(prompt, max_new_tokens=LLM_MAX_TOKENS, stop=["</s>", "<|user|>"])
                return _parse_facts(_clean_output(raw_output))
            except Exception:
                return []
        return []

    # --- Existing fallback chain (unchanged when HOST_LLM_ENABLED=false) ---
    raw_output = None
    if LLM_BASE_URL:
        raw_output = _call_remote_llm(prompt)
        if raw_output:
            facts = _parse_facts(_clean_output(raw_output))
            if facts:
                return facts

    llm = _load_llm()
    if llm is not None:
        try:
            raw_output = llm(prompt, max_new_tokens=LLM_MAX_TOKENS, stop=["</s>", "<|user|>"])
            return _parse_facts(_clean_output(raw_output))
        except Exception:
            return []

    return []
```

**Fix `llm_available()` (`local_llm.py:269-277`) — decision A5:**

Today `llm_available()` returns False for a Hermes-only user (no `LLM_BASE_URL`, no GGUF). Sleep gates on this in `beam.py:2134` and would skip the host path entirely. One-line fix at the top of `llm_available()`:

```python
def llm_available() -> bool:
    if LLM_ENABLED and HOST_LLM_ENABLED:
        from mnemosyne.core.llm_backends import get_host_llm_backend
        if get_host_llm_backend() is not None:
            return True
    if LLM_BASE_URL:
        return True
    if _llm_available is not None:
        return _llm_available
    _load_llm()
    return bool(_llm_available)
```

**Honor `LLM_ENABLED` for the remote path (decision A2):**

The current code allows `MNEMOSYNE_LLM_ENABLED=false` to be silently bypassed by `LLM_BASE_URL`. Either:
- (a) preferred — gate the existing remote call with `LLM_ENABLED` so the env var actually disables every LLM-backed memory op, OR
- (b) acceptable — leave the pre-existing behavior alone and rewrite the checklist line in this PR's review checklist to be honest about scope.

Choice (a) is one extra `if LLM_ENABLED` guard on the remote path; choice (b) is a doc-only change. The plan recommends (a), but flag if the maintainer prefers a doc-only pass and defer (a) to the follow-up tracking the same `extraction.py:90` cleanup.

**Host-aware chunking (decision C6):**

`chunk_memories_by_budget()` uses `LLM_N_CTX` (default 2048). When `_try_host_llm` is the path, the budget should come from `HOST_LLM_N_CTX` (default 32000). Either pass an override into `chunk_memories_by_budget()` from the host path, or have `summarize_memories()` consult the right budget when `HOST_LLM_ENABLED=True` and a backend exists. Document the new env var in Task 5 (docs).

**Updated fallback chain comments:**

```text
0a. Host-provided LLM backend, only if MNEMOSYNE_HOST_LLM_ENABLED=true AND a backend is registered AND MNEMOSYNE_LLM_ENABLED=true.
    On host failure when host was enabled-and-attempted: skip step 1; go directly to step 2.
1.  Remote OpenAI-compatible API, if MNEMOSYNE_LLM_BASE_URL is set.
2.  llama-cpp-python.
3.  ctransformers.
4.  Return None / [] → caller fallback.
```

**Important:** Neither modification imports Hermes. Both import only Mnemosyne's own tiny registry.

**Test cases (illustrative — see test plan artifact for the full enumeration):**

```python
def test_summarize_memories_uses_host_when_enabled(monkeypatch):
    from mnemosyne.core.llm_backends import CallableLLMBackend, set_host_llm_backend

    monkeypatch.setattr(local_llm, "LLM_ENABLED", True)
    monkeypatch.setattr(local_llm, "HOST_LLM_ENABLED", True)
    monkeypatch.setattr(local_llm, "LLM_BASE_URL", "http://remote/v1")
    monkeypatch.setattr(local_llm, "LLM_MAX_TOKENS", 128)
    monkeypatch.setattr(local_llm, "HOST_LLM_PROVIDER", "openai-codex")
    monkeypatch.setattr(local_llm, "HOST_LLM_MODEL", "gpt-5.1-mini")

    calls = []
    def fake_complete(prompt, *, max_tokens, temperature, timeout, provider=None, model=None):
        calls.append((prompt, max_tokens, temperature, timeout, provider, model))
        return "Host summary."

    set_host_llm_backend(CallableLLMBackend("test", fake_complete))
    with patch.object(local_llm, "_call_remote_llm") as mock_remote:
        assert local_llm.summarize_memories(["Memory one"]) == "Host summary."
        mock_remote.assert_not_called()
        assert calls
        assert calls[0][2] == 0.3   # summarize uses 0.3
        assert calls[0][3] == local_llm.HOST_LLM_TIMEOUT
        assert calls[0][4:] == ("openai-codex", "gpt-5.1-mini")


def test_summarize_memories_skips_remote_on_host_miss_when_enabled(monkeypatch):
    """A3 contract: host enabled, host returns None → fall to local, NOT to remote."""
    from mnemosyne.core.llm_backends import CallableLLMBackend, set_host_llm_backend

    monkeypatch.setattr(local_llm, "LLM_ENABLED", True)
    monkeypatch.setattr(local_llm, "HOST_LLM_ENABLED", True)
    monkeypatch.setattr(local_llm, "LLM_BASE_URL", "http://remote/v1")
    set_host_llm_backend(CallableLLMBackend("test", lambda *a, **k: None))
    with patch.object(local_llm, "_call_remote_llm", return_value="Remote summary.") as mock_remote, \
         patch.object(local_llm, "_call_local_llm", return_value="Local summary.") as mock_local:
        assert local_llm.summarize_memories(["Memory one"]) == "Local summary."
        mock_remote.assert_not_called()
        mock_local.assert_called_once()


def test_summarize_memories_falls_through_to_remote_when_HOST_LLM_ENABLED_false(monkeypatch):
    """REGRESSION: existing remote behavior unchanged when host is off."""
    from mnemosyne.core.llm_backends import CallableLLMBackend, set_host_llm_backend

    monkeypatch.setattr(local_llm, "LLM_ENABLED", True)
    monkeypatch.setattr(local_llm, "HOST_LLM_ENABLED", False)
    monkeypatch.setattr(local_llm, "LLM_BASE_URL", "http://remote/v1")
    set_host_llm_backend(CallableLLMBackend("test", lambda *a, **k: "Host summary."))
    with patch.object(local_llm, "_call_remote_llm", return_value="Remote summary.") as mock_remote:
        assert local_llm.summarize_memories(["Memory one"]) == "Remote summary."
        mock_remote.assert_called_once()


def test_extract_facts_uses_host_with_temperature_zero(monkeypatch):
    """C2 contract: extract_facts forces temperature=0.0 for determinism."""
    # ... mirrors the summarize test; asserts calls[0][2] == 0.0 ...


def test_extract_facts_unchanged_when_HOST_LLM_ENABLED_false(monkeypatch):
    """REGRESSION: existing extract_facts behavior unchanged when host is off."""


def test_llm_available_returns_true_when_only_host_backend_registered(monkeypatch):
    """A5 contract: sleep gate works for Hermes-only users."""
    from mnemosyne.core.llm_backends import CallableLLMBackend, set_host_llm_backend

    monkeypatch.setattr(local_llm, "LLM_ENABLED", True)
    monkeypatch.setattr(local_llm, "HOST_LLM_ENABLED", True)
    monkeypatch.setattr(local_llm, "LLM_BASE_URL", "")
    set_host_llm_backend(CallableLLMBackend("test", lambda *a, **k: "x"))
    assert local_llm.llm_available() is True
```

**Verification:**

```bash
python -m pytest tests/test_local_llm.py tests/test_extraction.py tests/test_llm_backends.py -q
```

---

### Task 3 — Add Hermes adapter module in the Hermes integration layer

**Objective:** Keep Hermes-specific imports out of core and encapsulate Hermes LLM calling in one small adapter.

**Files:**
- Create: `hermes_memory_provider/hermes_llm_adapter.py`
- Test: `tests/test_hermes_llm_adapter.py`

**Implementation sketch:**

```python
# hermes_memory_provider/hermes_llm_adapter.py
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class HermesAuxLLMBackend:
    name = "hermes"
    task = "compression"  # Hermes' current best-fit auxiliary task for memory ops.

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

        try:
            response = call_llm(
                task=self.task,
                provider=provider,
                model=model,
                messages=[
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
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        except Exception as exc:
            logger.warning("Hermes aux LLM call failed; falling back: %s", exc)
            return None

        return _extract_content(response)


def _extract_content(response) -> Optional[str]:
    """Prefer Hermes' canonical response parser (handles reasoning models). Fall back to ad-hoc shapes."""
    # C5: prefer Hermes' canonical helper.
    try:
        from agent.auxiliary_client import extract_content_or_reasoning  # type: ignore
        text = extract_content_or_reasoning(response)
        if isinstance(text, str) and text.strip():
            return text.strip()
    except Exception:
        pass

    # Defensive fallbacks (older Hermes builds without the helper).
    try:
        content = response.choices[0].message.content
        if isinstance(content, str) and content.strip():
            return content.strip()
    except Exception:
        pass

    if isinstance(response, dict):
        try:
            content = response["choices"][0]["message"]["content"]
            if isinstance(content, str) and content.strip():
                return content.strip()
        except Exception:
            pass

    content = getattr(response, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()

    return None


def register_hermes_host_llm() -> bool:
    try:
        from mnemosyne.core.llm_backends import set_host_llm_backend
        set_host_llm_backend(HermesAuxLLMBackend())
        return True
    except Exception as exc:
        logger.debug("Failed to register Hermes host LLM backend: %s", exc)
        return False


def unregister_hermes_host_llm() -> None:
    """Symmetric unregistration for shutdown(). See decision C7."""
    try:
        from mnemosyne.core.llm_backends import set_host_llm_backend
        set_host_llm_backend(None)
    except Exception as exc:
        logger.debug("Failed to unregister Hermes host LLM backend: %s", exc)
```

**Why `task="compression"`:** Hermes already has per-task auxiliary routing for compression. Reusing that slot gives users a familiar config path and automatically inherits provider/auth/model updates. Optional `provider`/`model` arguments, when supplied from `MNEMOSYNE_HOST_LLM_PROVIDER` / `MNEMOSYNE_HOST_LLM_MODEL`, are passed as explicit `call_llm()` overrides so Mnemosyne can use a different host compression model than Hermes' own compression setting without copying credentials.

**Why `complete()` not `summarize()` (decision C3):** the same backend serves consolidation AND fact extraction; `summarize` would bake in the wrong abstraction. The caller, not the backend, picks the system prompt and content per task.

**Test cases:**

- Mock `agent.auxiliary_client.call_llm` and verify called with `task="compression"`, optional provider/model overrides, system/user messages, max_tokens, timeout.
- Hermes import unavailable (no `agent` module in `sys.modules`) → returns `None`, no exception. **Mock pattern:** inject a fake `agent` package into `sys.modules` before patching `agent.auxiliary_client.call_llm`; otherwise the import path itself fails and the test cannot reach the adapter behavior (decision T2).
- `call_llm` raises → `None`.
- Reasoning-model response (empty `message.content`, populated `reasoning`) → `extract_content_or_reasoning` extracts text; bespoke fallbacks would have returned `None`.
- Object response, dict response, `.content` attr response — all parsed correctly via fallbacks.
- `register_hermes_host_llm()` installs backend; `unregister_hermes_host_llm()` clears it.

**Verification:**

```bash
python -m pytest tests/test_hermes_llm_adapter.py -q
```

---

### Task 4 — Register and unregister Hermes backend from `hermes_memory_provider`

**Objective:** When Mnemosyne is running under Hermes, make the host LLM adapter available without changing default behavior. Actual host use requires `MNEMOSYNE_HOST_LLM_ENABLED=true`. On shutdown, unregister to prevent stale references when the same process later runs Mnemosyne outside Hermes (decision C7).

**Files:**
- Modify: `hermes_memory_provider/__init__.py`
- Test: `tests/test_hermes_memory_provider.py`

**Change locations:**

In `MnemosyneMemoryProvider.initialize()` (`hermes_memory_provider/__init__.py:238-256`), after the existing BeamMemory try/except (after line 256):

```python
        try:
            from hermes_memory_provider.hermes_llm_adapter import register_hermes_host_llm
            if register_hermes_host_llm():
                logger.info("Mnemosyne registered Hermes auxiliary LLM backend for memory operations")
        except Exception as exc:
            logger.debug("Mnemosyne could not register Hermes auxiliary LLM backend: %s", exc)
```

In `MnemosyneMemoryProvider.shutdown()` (`hermes_memory_provider/__init__.py:480`), before/after the existing `_beam` cleanup:

```python
        try:
            from hermes_memory_provider.hermes_llm_adapter import unregister_hermes_host_llm
            unregister_hermes_host_llm()
        except Exception as exc:
            logger.debug("Mnemosyne could not unregister Hermes auxiliary LLM backend: %s", exc)
```

**Rules:**

- This must not make the provider unavailable if Hermes LLM registration fails.
- This must not import `agent.auxiliary_client` at module import time; only inside the adapter call path.
- This must not alter non-Hermes package importability.

**Test cases:**

- `test_initialize_registers_host_llm_when_register_returns_true` — patch `register_hermes_host_llm` to True; assert `initialize()` succeeds and `get_host_llm_backend()` is not None.
- `test_initialize_does_not_fail_when_register_raises` — patch to raise; assert `initialize()` still succeeds.
- `test_shutdown_clears_host_backend` — register, then call `shutdown()`; assert `get_host_llm_backend()` is None.
- `test_initialize_unchanged_for_non_host_paths` — REGRESSION: BeamMemory init still works; log order preserved.

---

### Task 5 — Bound `on_session_end()` so the host LLM cannot block Hermes shutdown

**Objective:** Apply the existing `_maybe_auto_sleep()` daemon-thread pattern to `on_session_end()` so a stalling network LLM call cannot block session shutdown indefinitely. Closes a pre-existing bug in the same file and prevents this PR from making the synchronous-on-shutdown behavior worse (decision A6).

**Files:**
- Modify: `hermes_memory_provider/__init__.py`
- Test: `tests/test_hermes_memory_provider.py`

**Background:** `_maybe_auto_sleep()` at lines 331-344 of the same file already runs `sleep` in a `threading.Thread(daemon=True)` with a 5-second `join(timeout=5)` and a warning log on timeout. Mid-session auto-sleep is bounded today. `on_session_end()` at lines 457-464 does NOT use that pattern — it calls `self._beam.sleep()` directly and waits for it to return, which is the path that makes Hermes' shutdown wait on the host LLM.

**Change:**

```python
def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
    if not self._beam:
        return
    try:
        logger.info("Mnemosyne session end — running consolidation")
        sleep_thread = threading.Thread(target=self._beam.sleep, daemon=True)
        sleep_thread.start()
        sleep_thread.join(timeout=15)
        if sleep_thread.is_alive():
            logger.warning("Mnemosyne session-end sleep timed out after 15s — consolidation deferred")
    except Exception as e:
        logger.debug("Mnemosyne session-end sleep failed: %s", e)
```

15 seconds (rather than the 5 used in `_maybe_auto_sleep`) is the chosen budget because session-end consolidation is the last opportunity to write episodic memories before the process exits; mid-session auto-sleep can be cheaper because it will run again on the next 10-turn boundary.

**Test:**

- `test_on_session_end_returns_within_timeout_when_sleep_blocks` — patch `self._beam.sleep` to `time.sleep(30)`; assert `on_session_end()` returns in under 16 seconds.
- `test_on_session_end_logs_warning_on_timeout` — same setup; assert the timeout warning is emitted.

---

### Task 6 — Add an optional standalone host-plugin registration path

**Objective:** Make the new interface useful for future non-Hermes agents without another refactor.

**Files:**
- Modify or document only: `mnemosyne/core/llm_backends.py`
- Optional docs: `README.md` or `docs/` if project uses docs.

**Design:**

No plugin auto-discovery is necessary in the first surgical change. The extension point is simply:

```python
from mnemosyne.core.llm_backends import set_host_llm_backend
set_host_llm_backend(MyAgentBackend())
```

A future agent integration can implement:

```python
class MyAgentBackend:
    name = "my-agent"
    def complete(self, prompt, *, max_tokens, temperature, timeout, provider=None, model=None): ...
```

This mirrors Hermes' modular provider-profile direction but avoids building a full provider registry inside Mnemosyne before there is a second host integration.

**Why not auto-discovery now:** It would be over-engineering. The only current need is Hermes; a simple setter is enough and remains compatible with later entry-point discovery.

---

### Task 7 — Add docs for users and maintainers

**Objective:** Explain behavior clearly enough that users do not try to paste tokens or configure Codex as a raw endpoint.

**Files:**
- Modify: `README.md` or create `docs/hermes-llm-integration.md`
- Modify: `hermes_memory_provider/__init__.py` docstring if desired.

**Documentation points:**

```markdown
### Hermes auxiliary LLM integration

When Mnemosyne runs as a Hermes memory provider, it can optionally use Hermes'
auxiliary LLM routing for both memory consolidation (sleep) AND structured fact
extraction. This lets Mnemosyne use the same provider/auth/model configuration as
Hermes, including OAuth-backed providers such as `openai-codex`.

This path is disabled by default to preserve existing Mnemosyne behavior after
upgrade. To use the Hermes/host LLM backend, set:

```bash
# Optional host override.
# When true and a host backend is available, Mnemosyne uses the host-provided
# LLM instead of MNEMOSYNE_LLM_BASE_URL / MNEMOSYNE_LLM_API_KEY /
# MNEMOSYNE_LLM_MODEL. Leave unset/false to preserve existing remote/local behavior.
MNEMOSYNE_HOST_LLM_ENABLED=true

# Optional: override the host default compression provider/model for Mnemosyne calls.
# Leave unset to let Hermes use auxiliary.compression.* or its normal auto/default routing.
# These are not credentials; Hermes still owns auth, OAuth refresh, and transport.
# MNEMOSYNE_HOST_LLM_PROVIDER=openai-codex
# MNEMOSYNE_HOST_LLM_MODEL=gpt-5.1-mini

# Optional: prompt context budget when the host backend is the chosen path.
# Default 32000. The existing MNEMOSYNE_LLM_N_CTX (default 2048) is calibrated
# for TinyLlama and is too small for typical Codex/GPT context windows.
# MNEMOSYNE_HOST_LLM_N_CTX=32000

# Behavior when host is enabled:
#   1. If a host backend is registered, Mnemosyne calls it.
#   2. If the host returns text, Mnemosyne uses it (does NOT consult MNEMOSYNE_LLM_BASE_URL).
#   3. If the host returns nothing or errors, Mnemosyne falls to the local GGUF backend
#      (still does NOT consult MNEMOSYNE_LLM_BASE_URL — the remote URL is bypassed
#      whenever host is enabled, to prevent accidentally routing memory content to a
#      stale URL the user forgot to clear).
#   4. If neither host nor local produces content, the existing non-LLM fallback runs.
```

Mnemosyne does not read Hermes tokens and does not require `MNEMOSYNE_LLM_API_KEY`
for this path. It calls Hermes' `agent.auxiliary_client.call_llm(task="compression", ...)`,
so Hermes handles auth, provider quirks, Codex Responses API translation, and fallback.

To control the default host model without Mnemosyne-specific overrides, configure
Hermes:

```yaml
auxiliary:
  compression:
    provider: auto        # default; uses main provider/model first
    model: ""             # empty inherits Hermes behavior
    timeout: 15           # per-attempt; Hermes may retry internally
```

The `timeout` is per-attempt; Hermes can retry for auth refresh, payment fallback,
or provider fallback, so total wall-clock can exceed it on cold start.

For `openai-codex`, do NOT set `MNEMOSYNE_LLM_BASE_URL` to
`https://chatgpt.com/backend-api/codex`. Use Hermes login/model configuration instead.

### Session shutdown

Mnemosyne's `on_session_end()` runs sleep/consolidation in a daemon thread with a
15-second join timeout. If consolidation cannot finish in time (e.g., a slow host
LLM call), it is deferred — Hermes shutdown is not blocked. A warning is logged
when this happens.
```

---

### Task 8 — Verify failure modes and timeouts

**Objective:** Prove this cannot recreate known Hermes TUI/gateway stalls.

**Files:**
- Tests added in Tasks 1, 2, 3, 4, 5.
- `tests/conftest.py` — autouse `_reset_host_llm_backend` fixture (decision T1).

**Required failure-mode tests:**

1. Hermes import unavailable → returns `None`.
2. `call_llm` raises `RuntimeError` → returns `None`.
3. response has no content → returns `None`.
4. timeout argument is passed through to `call_llm`.
5. host backend exception does not prevent local fallback (when host enabled, remote is bypassed; local is consulted).
6. `MNEMOSYNE_LLM_ENABLED=false` disables host backend regardless of `HOST_LLM_ENABLED` and registration.
7. `HOST_LLM_ENABLED=true` with no backend registered → behavior identical to `HOST_LLM_ENABLED=false` (remote/local fallback chain intact).
8. `on_session_end()` returns within ~16 seconds even when `self._beam.sleep` blocks indefinitely.
9. `shutdown()` clears the host backend.

**Conftest fixture:**

Append an autouse fixture to `tests/conftest.py` mirroring the existing pattern at lines 52-66:

```python
@pytest.fixture(autouse=True)
def _reset_host_llm_backend():
    """Defense in depth on top of per-test try/finally."""
    try:
        from mnemosyne.core import llm_backends
        llm_backends._backend = None
    except Exception:
        pass
    yield
    try:
        from mnemosyne.core import llm_backends
        llm_backends._backend = None
    except Exception:
        pass
```

**Manual verification commands:**

```bash
python -m pytest tests/test_llm_backends.py tests/test_local_llm.py tests/test_extraction.py tests/test_hermes_llm_adapter.py tests/test_hermes_memory_provider.py -q
```

If the repo has a full suite target, run:

```bash
python -m pytest -q
```

**Manual end-to-end (requires a Hermes install with openai-codex configured):**

1. Configure Hermes: `auxiliary.compression.provider: openai-codex`.
2. Set `MNEMOSYNE_HOST_LLM_ENABLED=true`.
3. Run a session that calls `remember(content, extract=True)`.
4. Verify Hermes log shows `call_llm(task='compression', ...)` routed to `openai-codex`.
5. Verify Mnemosyne's facts table received the extracted facts.
6. End the session and verify `on_session_end` completes (or logs the 15-second timeout) without blocking Hermes shutdown.

**Manual standalone verification (regression):**

```bash
# In a fresh venv with no Hermes:
pip install mnemosyne
python -c "from mnemosyne import remember, recall; remember('test'); print(recall('test'))"
# Confirm no behavior change vs current main.
```

---

## Suggested code review checklist

- [ ] `mnemosyne/core` does not import `agent.*`, `hermes_cli.*`, or read `~/.hermes/auth.json`.
- [ ] Hermes adapter imports `agent.auxiliary_client` only inside the call path.
- [ ] `MNEMOSYNE_LLM_BASE_URL` behavior is unchanged when `MNEMOSYNE_HOST_LLM_ENABLED` is unset/false.
- [ ] Local GGUF fallback behavior is unchanged when `MNEMOSYNE_HOST_LLM_ENABLED` is unset/false.
- [ ] `MNEMOSYNE_LLM_ENABLED=false` disables all LLM-backed memory operations (host, remote, local). If choice (b) was taken in A2, this checklist line says "host and local only" instead, with a follow-up TODO referenced.
- [ ] `MNEMOSYNE_HOST_LLM_ENABLED=true` with a registered backend uses the host backend. On host failure, falls to local GGUF — never to `MNEMOSYNE_LLM_BASE_URL`.
- [ ] `MNEMOSYNE_HOST_LLM_ENABLED=true` with NO registered backend behaves identically to host disabled (existing remote/local fallback intact).
- [ ] Optional `MNEMOSYNE_HOST_LLM_PROVIDER` / `MNEMOSYNE_HOST_LLM_MODEL` are passed to the host backend only when non-empty, and are ignored when host LLM is disabled.
- [ ] `MNEMOSYNE_HOST_LLM_N_CTX` (default 32000) governs prompt budget when host is the chosen path.
- [ ] `summarize_memories` uses `temperature=0.3`; `extract_facts` uses `temperature=0.0`.
- [ ] Backend protocol method is `complete()`, not `summarize()`.
- [ ] Adapter prefers `extract_content_or_reasoning()` from Hermes; falls back to bespoke parsing only on import failure.
- [ ] `_build_host_prompt()` (and host extraction prompt builder if added) emit no TinyLlama chat-template tokens.
- [ ] No secrets are logged.
- [ ] Tests use mocks only; no live calls. Tests inject a fake `agent` package into `sys.modules` before patching `agent.auxiliary_client.call_llm`.
- [ ] `conftest.py` includes an autouse `_reset_host_llm_backend` fixture.
- [ ] Failure falls back per A3, never crashes.
- [ ] Timeout default is 10-15 seconds **per attempt**; total wall-clock may be 2-3× under retry.
- [ ] `MnemosyneMemoryProvider.shutdown()` calls `unregister_hermes_host_llm()`.
- [ ] `on_session_end()` runs sleep in a daemon thread with a 15-second join timeout.
- [ ] `llm_available()` returns True when only a host backend is registered (Hermes-only users).

---

## Open questions for maintainer discussion

1. Is `MNEMOSYNE_HOST_LLM_ENABLED=true` acceptable as the one surgical opt-in for host/Hermes LLM use, with unset/false preserving existing remote/local/fallback behavior exactly?
2. Are optional non-secret `MNEMOSYNE_HOST_LLM_PROVIDER` / `MNEMOSYNE_HOST_LLM_MODEL` acceptable as per-call overrides for users who want Mnemosyne's host model to differ from Hermes' default `auxiliary.compression` model?
3. **A2 follow-on:** today `MNEMOSYNE_LLM_ENABLED=false` does NOT actually gate the existing remote `LLM_BASE_URL` path. This plan recommends fixing that as part of the PR (one extra `if LLM_ENABLED` guard) so the env var honestly disables every LLM-backed memory op. Acceptable as a small in-scope cleanup, or do you prefer a doc-only pass that defers the cleanup to a TODO?
4. **A4 boundary:** `mnemosyne/extraction/client.py` (the OpenRouter `ExtractionClient` used only by `tools/evaluate_beam_end_to_end.py --use-cloud`) is left out of this PR. Routing it through the host backend is a separate change. Acceptable?
5. Should the adapter use Hermes `task="compression"` or introduce `task="memory"` / `task="memory_consolidation"` in Hermes? This plan recommends `compression` now to avoid requiring a Hermes core change.
6. Should Mnemosyne expose a public API like `mnemosyne.configure(llm_backend=...)` later? Not needed for the surgical change, but it may be cleaner long-term.
7. **A6 follow-on:** the bounded `on_session_end()` in this PR caps consolidation at 15 seconds during shutdown. The pre-existing `_maybe_auto_sleep()` mid-session path uses 5 seconds. Are these the right defaults, or should both be tunable via env var?

---

## Recommended first PR scope

Keep the first PR small but complete-enough to honor the "shared backend" claim end-to-end:

1. `mnemosyne/core/llm_backends.py` (new — Task 1).
2. `mnemosyne/core/local_llm.py` modifications (Task 2): `_try_host_llm` helper, `_build_host_prompt`, host insertion in `summarize_memories`, `llm_available()` fix, host-aware chunking budget, optional A2 (a) `LLM_ENABLED` remote guard.
3. `mnemosyne/core/extraction.py` modifications (Task 2): host insertion in `extract_facts`, `temperature=0.0`.
4. `hermes_memory_provider/hermes_llm_adapter.py` (new — Task 3): `HermesAuxLLMBackend.complete`, `extract_content_or_reasoning` integration with bespoke fallback, `register_hermes_host_llm` and `unregister_hermes_host_llm`.
5. `hermes_memory_provider/__init__.py` modifications (Task 4 + Task 5): registration in `initialize()`, unregistration in `shutdown()`, bounded `on_session_end()`.
6. `tests/conftest.py` autouse `_reset_host_llm_backend` fixture (Task 8).
7. Focused tests: `tests/test_llm_backends.py`, `tests/test_local_llm.py` updates, `tests/test_extraction.py` (new), `tests/test_hermes_llm_adapter.py` (new), `tests/test_hermes_memory_provider.py` (new).
8. Docs (Task 7).

Avoid touching installer, database paths, embeddings, triples, the OpenRouter `ExtractionClient`, MCP tooling, or Hermes source.

---

## Expected outcome

After this change:

- A Hermes user with `auxiliary.compression.provider: openai-codex` and a valid Hermes login can run Mnemosyne consolidation AND fact extraction through Hermes' authenticated auxiliary client.
- A Hermes user can choose a small aux compression model such as `gpt-5.4-mini` through Hermes config without adding Mnemosyne-specific API keys.
- A Hermes-only user (no `MNEMOSYNE_LLM_BASE_URL`, no local GGUF) is supported: `llm_available()` returns True when the host backend is registered, so sleep actually runs.
- A non-Hermes user sees existing behavior unchanged.
- Hermes session shutdown does not block on a slow LLM call: `on_session_end()` is bounded to 15 seconds.
- A process that uses Mnemosyne after Hermes shutdown does not retain a stale Hermes backend — `shutdown()` unregisters it.
- Future non-Hermes agents can inject their own backend through the same small interface without refactoring Mnemosyne core again.

---

## Decision log (eng review revision, 2026-05-07)

Decisions captured during the `/plan-eng-review` walk-through. Each entry below ties a code/doc change to the issue it resolves.

| ID | Area | Decision | Rationale |
|----|------|----------|-----------|
| **A1** | scope | Extend Task 2 to also patch `extract_facts()`, not only `summarize_memories()`. | Plan's own maintainer-clarification said the host adapter must serve both consolidation AND fact extraction; original Task 2 only covered consolidation, leaving fact extraction stuck on `MNEMOSYNE_LLM_BASE_URL`. |
| **A2** | gate semantics | Add explicit `LLM_ENABLED` guard at the top of `_try_host_llm`. Recommend also closing the pre-existing gap on the remote `LLM_BASE_URL` path. | Today `LLM_BASE_URL` bypasses `LLM_ENABLED`; the checklist would be lying without one of (a) a real fix or (b) a doc rewrite. |
| **A3** | fallback precedence | When host is enabled and attempted, on failure skip `MNEMOSYNE_LLM_BASE_URL` and go straight to local GGUF, then None. | Prevents leaking memory content to a stale remote URL when the user enabled host specifically to avoid that path. |
| **A4** | boundary | OpenRouter `ExtractionClient` (`--use-cloud`) is out of scope. | Separate code path with separate credentials; routing it through host is a follow-on PR. |
| **A5** | call-graph | Teach `llm_available()` to consider a registered host backend. | Without this, `beam.py:2134` short-circuits before host gets a chance for any user without a remote URL or local GGUF. Single-line fix; centralized. |
| **A6** | blast radius | Apply the existing `_maybe_auto_sleep` daemon-thread pattern to `on_session_end()` with a 15s join. | Pre-existing bug the user has hit; this PR makes shutdown blocking worse if not fixed. 5-line surgical fix using a pattern already in the file. |
| **C1** | DRY / API shape | Factor a private `_try_host_llm` helper returning `(attempted, text)` sentinel tuple. | Two call sites (Task 2) need identical behavior; `Optional[str]` alone cannot encode "no backend" vs "backend attempted-and-failed", which A3 requires. |
| **C2** | quality | Per-call temperature: 0.0 for `extract_facts`, 0.3 for `summarize_memories`. | Determinism for fact extraction prevents near-duplicate writes to the facts table on re-ingest. |
| **C3** | naming | Rename backend method `summarize()` → `complete()`. | After A1, the same backend serves consolidation and extraction; `summarize` is the wrong abstraction. |
| **C4** | quality | Add `_build_host_prompt()` (and host extraction prompt builder if needed) emitting plain text without TinyLlama chat-template tokens. | `_build_prompt()` emits `<|user|>`/`</s>`/`<|assistant|>` for TinyLlama; sending those to Codex degrades output. |
| **C5** | reuse | Use Hermes' `extract_content_or_reasoning()` first; bespoke parser as defensive fallback. | Reasoning-model responses with empty `message.content` are valid in Hermes; bespoke parser would treat them as failure. |
| **C6** | quality | Add `MNEMOSYNE_HOST_LLM_N_CTX` (default 32000) for host-aware chunking budget. | TinyLlama-calibrated `LLM_N_CTX=2048` produces wastefully many small chunks and lossy multi-chunk summaries on 128K-context aux models. |
| **C7** | lifecycle | `MnemosyneMemoryProvider.shutdown()` unregisters the host backend. | Symmetric with `initialize()` registration; prevents stale Hermes backend from being called by a later standalone Mnemosyne use in the same process. |
| **C8** | cosmetic | Renumber duplicate "Task 5" sections in the original plan. (This revision already does so: Tasks 1-8 with no duplicates.) | Plan-doc cleanliness for the maintainer. |
| **T1** | tests | Autouse `_reset_host_llm_backend` fixture in `tests/conftest.py`. | Defense in depth on top of per-test try/finally; future tests can't accidentally bleed `_backend` state. |
| **T2** | tests | Inject fake `agent` package into `sys.modules` before patching `agent.auxiliary_client.call_llm` in tests. | Hermes is not a test-time dependency; without the fake module the import path itself fails before adapter behavior can be exercised. |

---
