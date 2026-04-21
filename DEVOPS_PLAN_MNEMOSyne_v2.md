# Mnemosyne v2 Enhancement — DevOps Plan

**Initiative:** Recall Tracking & Smart Scoring  
**Owner:** Abdias J  
**Date:** 2026-04-19  
**Status:** IN PROGRESS

---

## 1. Objective

Add behavioral memory tracking (recall counts, timestamps) and recency-aware scoring to Mnemosyne's BEAM architecture. This makes the memory system feel stateful rather than just searchable — memories that are frequently recalled stay hot; stale memories decay naturally.

---

## 2. Scope

| Phase | Feature | Files | Risk |
|---|---|---|---|
| 1 | Recall tracking schema + migration | `beam.py` | Low — additive only |
| 2 | Recency decay in recall scoring | `beam.py` | Low — scoring change only |
| 3 | Semantic deduplication on write | `beam.py`, `memory.py` | Medium — affects write path |
| 4 | Plugin tool updates | `hermes_plugin/tools.py` | Low — additive params |
| 5 | Integration test + live DB migration | `tests/`, live DB | Medium — schema change on prod |
| 6 | Backup, deploy, verify | `dr/`, git | Low — DR exists |
| 7 | Local LLM consolidation (replaces aaak) | `local_llm.py`, `beam.py` | Medium — new dependency, optional |
| 8 | **Temporal validity + invalidation** | `beam.py` | Medium — schema + query changes |
| 9 | **Cross-session global memory** | `beam.py` | Medium — scope filtering in recall |

**Out of scope:** Auto-context injection (requires Hermes core changes), graph edge traversal.

---

## 3. Architecture

```
┌─────────────────┐     ┌──────────────────┐
│  Hermes Agent   │────▶│ mnemosyne_recall │
└─────────────────┘     └──────────────────┘
           │                       │
           ▼                       ▼
    ┌─────────────┐       ┌────────────────┐
    │  remember   │       │  recall()      │
    │  +dedup     │       │  +recall_count │
    │  +vec_check │       │  +last_recalled│
    └─────────────┘       │  +time_decay   │
           │              └────────────────┘
           ▼                       │
    ┌─────────────┐              ▼
    │ working_mem │◄──────┌──────────────┐
    │  recall_count      │ episodic_mem │
    │  last_recalled     │  recall_count│
    └─────────────┘      │  last_recalled
                         └──────────────┘
```

---

## 4. Schema Changes

### 4.1 New columns (additive, nullable defaults)

```sql
-- working_memory
ALTER TABLE working_memory ADD COLUMN recall_count INTEGER DEFAULT 0;
ALTER TABLE working_memory ADD COLUMN last_recalled TIMESTAMP DEFAULT NULL;

-- episodic_memory
ALTER TABLE episodic_memory ADD COLUMN recall_count INTEGER DEFAULT 0;
ALTER TABLE episodic_memory ADD COLUMN last_recalled TIMESTAMP DEFAULT NULL;
```

### 4.2 Migration strategy

SQLite `ALTER TABLE ADD COLUMN` is online and safe. No data rewrite. Columns default to 0/NULL for existing rows.

---

## 5. Implementation Details

### 5.1 Recall tracking

Inside `BeamMemory.recall()`:
1. Collect all returned memory IDs
2. After sorting + slicing to `top_k`, run UPDATE:
   ```sql
   UPDATE working_memory SET recall_count = recall_count + 1, last_recalled = ? WHERE id = ?;
   UPDATE episodic_memory SET recall_count = recall_count + 1, last_recalled = ? WHERE rowid = ?;
   ```

### 5.2 Recency decay

Add a time-decay factor to the final score:

```python
hours_old = max(0, (now - parsed_timestamp).total_seconds() / 3600)
decay = exp(-hours_old / HALFLIFE_HOURS)  # e.g., halflife = 168h (1 week)
final_score = (base_score * 0.7) + (decay * 0.3)
```

This keeps high-importance memories from being drowned by brand-new low-value noise, while still letting recency matter.

### 5.3 Semantic deduplication

Inside `BeamMemory.remember()`:
1. Embed the new content
2. Query `vec_episodes` for top-1 similarity
3. If similarity > 0.92, UPDATE existing row instead of INSERT
4. Fall back to exact content match if embeddings unavailable

### 5.4 Local LLM consolidation (Phase 7)

**Goal:** Replace lossy aaak compression with actual semantic summarization via a tiny local model.

**Model:** TinyLlama-1.1B-Chat-v1.0-GGUF (Q4_K_M, ~640MB)  
**Runtime:** `ctransformers` (CPU-only, no GPU/CUDA required)  
**Model cache:** `~/.hermes/mnemosyne/models/`  
**Download:** On-demand via HuggingFace  
**Fallback:** If LLM unavailable, model missing, or inference fails → fall back to aaak encoding

**Architecture:**
```
┌─────────────────┐
│  sleep()        │
│  (beam.py)      │
└────────┬────────┘
         │
    ┌────▼────┐
    │ local   │
    │ _llm    │
    │ .py     │
    └────┬────┘
         │
    ┌────▼────┐     ┌──────────────┐
    │ ctrans  │     │ aaak_encode  │
    │ formers │     │ (fallback)   │
    └────┬────┘     └──────────────┘
         │
    ┌────▼────┐
    │ episodic│
    │ _memory │
    └─────────┘
```

**Config via env vars:**
```
MNEMOSYNE_LLM_ENABLED=true
MNEMOSYNE_LLM_MAX_TOKENS=256
MNEMOSYNE_LLM_N_THREADS=4
MNEMOSYNE_LLM_REPO=TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF
MNEMOSYNE_LLM_FILE=tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf
```

### 5.5 Temporal validity + invalidation (Phase 8)

**Goal:** Memories like "Cartesia API key is X" have a shelf life. Allow setting `valid_until` and marking memories as superseded.

**Schema:**
```sql
ALTER TABLE working_memory ADD COLUMN valid_until TIMESTAMP DEFAULT NULL;
ALTER TABLE working_memory ADD COLUMN superseded_by TEXT DEFAULT NULL;
ALTER TABLE episodic_memory ADD COLUMN valid_until TIMESTAMP DEFAULT NULL;
ALTER TABLE episodic_memory ADD COLUMN superseded_by TEXT DEFAULT NULL;
```

**Behavior:**
- `remember(..., valid_until="2026-12-31T00:00:00")` → memory auto-expires after that date
- `recall()` filters out `valid_until < now()` AND `superseded_by IS NOT NULL`
- `invalidate(memory_id)` sets `superseded_by = new_memory_id` or `valid_until = now()`
- Triple store already has `valid_from` — we extend this to the memory tables

### 5.6 Cross-session global memory (Phase 9)

**Goal:** User preferences should travel across sessions. Conversation-specific context stays bounded.

**Schema:**
```sql
ALTER TABLE working_memory ADD COLUMN scope TEXT DEFAULT 'session';
ALTER TABLE episodic_memory ADD COLUMN scope TEXT DEFAULT 'session';
```

**Behavior:**
- `remember(..., scope="global")` → visible in all sessions
- `recall()` searches: current `session_id` + all `scope='global'` memories
- `get_context()` injects global memories first, then session memories
- Default scope is `"session"` for backward compatibility

---

## 6. Testing Strategy

| Test | Method |
|---|---|
| Schema migration | Run `init_beam()` against a copy of the live DB |
| Recall tracking | Store memory → recall → verify counts incremented |
| Recency decay | Store two identical memories 1 hour apart, recall should rank newer higher |
| Deduplication | Store same content twice, verify only one row |
| Backward compat | Verify old `mnemosyne_recall` calls still work unchanged |
| **LLM consolidation** | Run `sleep()` with local model → verify readable summaries in episodic_memory |
| **LLM fallback** | Remove model file → verify sleep() falls back to aaak |
| **LLM memory** | Verify model loads once, subsequent calls reuse instance |

---

## 7. Rollback Plan

1. **Pre-change backup:** `cp mnemosyne.db mnemosyne.db.pre-v2-$(date +%s)`
2. **Git revert:** All changes are in git; `git checkout -- .` restores code
3. **Schema revert:** SQLite doesn't support `DROP COLUMN`; if critical, restore from backup
4. **DR fallback:** Disaster recovery in `mnemosyne/dr/` can restore from latest auto-backup

---

## 8. Success Criteria

- [x] `mnemosyne_recall` increments `recall_count` on accessed memories
- [x] `last_recalled` is set to ISO timestamp on access
- [x] Recency decay visibly affects ranking in mixed-age recall tests
- [x] No regression in existing recall accuracy
- [x] Live DB migration completes without data loss
- [x] Plugin tools pass smoke test
- [x] `sleep()` produces human-readable summaries via local LLM
- [x] LLM fallback to aaak works when model unavailable
- [x] Model loads once and reuses across sleep() calls
- [x] No cloud API calls during consolidation
- [x] `valid_until` auto-filters expired memories from recall
- [x] `invalidate()` marks memories as expired/superseded
- [x] `scope='global'` memories visible across all sessions
- [x] `get_context()` prioritizes global memories
- [x] Backward compatibility: old calls without valid_until/scope still work

---

## 9. Execution Log

| Step | Status | Timestamp |
|---|---|---|
| Plan created | DONE | 2026-04-19 02:10 UTC |
| Phase 1: Schema + migration | DONE | 2026-04-19 02:20 UTC |
| Phase 2: Recency decay | DONE | 2026-04-19 02:20 UTC |
| Phase 3: Deduplication | DONE | 2026-04-19 02:20 UTC |
| Phase 4: Plugin updates | N/A — no API changes |  |
| Phase 5: Test + migrate | DONE | 2026-04-19 02:27 UTC |
| Phase 6: Deploy + verify | DONE | 2026-04-19 02:27 UTC |
| Phase 7: Local LLM consolidation | DONE | 2026-04-19 02:39 UTC |
| Phase 8: Temporal validity + invalidation | DONE | 2026-04-19 02:42 UTC |
| Phase 9: Cross-session global memory | DONE | 2026-04-19 02:42 UTC |
