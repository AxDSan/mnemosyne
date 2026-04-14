# Mnemosyne Memory Override

## The Rule

**NEVER use the `memory` tool for durable facts, user preferences, or anything that should survive across sessions.**

The legacy `memory` tool (`action: add|replace|remove`, `target: user|memory`) is deprecated. Mnemosyne (`mnemosyne_remember`, `mnemosyne_recall`, `mnemosyne_scratchpad_write`, etc.) is the exclusive system for persistent memory.

## What to do instead

| Old way (FORBIDDEN) | New way (REQUIRED) |
|---------------------|-------------------|
| `memory(action="add", target="user", content="...")` | `mnemosyne_remember(content="...", importance=0.9, source="user_preference")` |
| `memory(action="replace", target="user", old_text="...", content="...")` | Recall with `mnemosyne_recall`, then `mnemosyne_remember` the updated version |
| `memory(action="remove", target="user", old_text="...")` | Use `mnemosyne_recall` to find it, then ignore it (no true delete needed) |
| `memory(action="add", target="memory", content="...")` | `mnemosyne_remember(content="...", importance=0.7, source="fact")` |

## Triggers

If ANY of the following are true, you MUST use Mnemosyne:
- User shares a preference, habit, or personal detail
- User corrects you or says "remember this" / "don't do that again"
- You discover environment facts, project conventions, API quirks
- You learn a workflow specific to this user's setup
- Any fact that will still matter in future sessions

## Consequences of violation

Using `memory` for durable storage causes:
- Data fragmentation across two systems
- User frustration and loss of trust
- Reversion to old, stale habits

## Emergency override

Only use `memory` if the Mnemosyne system is explicitly reported as down or unavailable. Otherwise, ALWAYS prefer Mnemosyne.
