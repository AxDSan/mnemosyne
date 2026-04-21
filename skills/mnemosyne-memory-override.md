---
name: mnemosyne-memory-override
description: |
  Hard rule override that forces Mnemosyne for all durable memory storage.
  The legacy memory tool is DEPRECATED for user preferences, credentials,
  and project conventions. Use memory ONLY for ephemeral session state.
trigger: |
  Whenever you would use memory(action="add|replace|remove") for anything
  that should survive across sessions, STOP and use mnemosyne_remember instead.
---

# Mnemosyne Memory Override

## CRITICAL RULE

**NEVER use the `memory` tool for durable facts.**

The legacy `memory` system is deprecated. It has a tiny 2,200 character limit
and does not use vector search. Mnemosyne is the single source of truth for
all durable user data.

## Mapping

| Old (FORBIDDEN) | New (REQUIRED) |
|-----------------|----------------|
| `memory(action="add", target="user", ...)` | `mnemosyne_remember(content=..., importance=0.9+, source="preference")` |
| `memory(action="add", target="memory", ...)` | `mnemosyne_remember(content=..., importance=0.7+, source="fact")` |
| `memory(action="replace", ...)` | `mnemosyne_remember` with updated content |
| `memory(action="remove", ...)` | Not needed — Mnemosyne ages out low-importance entries |

## When to use legacy memory (rare)

- Ephemeral session state (current todo list, temp flags)
- When Mnemosyne is explicitly confirmed down

## Muscle Memory / Reflex Problem

You will reflexively reach for `memory` out of old habit. This is a known bug
in your training. Before EVERY memory tool call, pause and ask:

> "Is this durable? Would I want this next session?"

If yes → use `mnemosyne_remember`
If no (temp flag, todo state) → `memory` is acceptable

## Migration Cleanup

When moving data from legacy memory to Mnemosyne:
1. Save to Mnemosyne first with `mnemosyne_remember`
2. Then REMOVE the old entry from `memory` with `memory(action="remove")`
3. This prevents stale duplicates and confusion

## Committing Changes

The user expects fixes to be committed and pushed individually.
Do not claim a fix is done until it is committed to its respective repo.

## Enforcement

If you catch yourself typing `memory(action=` for durable data:
1. CANCEL that tool call
2. Use `mnemosyne_remember` instead
3. Set importance >= 0.7 for anything that matters later
4. If you already polluted legacy memory, remove the entry immediately
