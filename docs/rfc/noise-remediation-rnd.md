# R&D: Addressing Existing Noise in Mnemosyne Memory Databases

**Date:** 2026-07-08
**Owner:** abdiisan
**Status:** Open / Exploratory
**Priority:** High (community pain point)
**Repo:** mnemosyne-oss/mnemosyne

---

## 1. R&D Summary: Key Findings from Codebase Exploration

### 1.1 The noise remediation framework already exists (mostly built)

The biggest finding: Mnemosyne already has a **three-layer noise remediation system** that was built as part of issues #406 and #428. It is not a gap that needs to be built from scratch — it is a gap in **coverage, integration, and discoverability**.

**Layer 1: Pre-storage write filter** (`mnemosyne/core/filters.py:240-371`)
- `classify_memory_write()` — deterministic classifier with 3 stages:
  1. Secret detection (10 labeled regex patterns: API keys, AWS, GitHub, Slack, Google, JWT, password assignments, private keys, connection strings, env secrets)
  2. Curated noise patterns (18 patterns: terminal output, heartbeats, stack traces, transient status, empty/trivial content)
  3. Structural heuristics (high line count + low semantic structure = likely dump)
- `should_remember()` — the main entry point, called from `memory.py:336-340` before any write
- Returns `WriteDecision(action="allow"|"reject"|"rewrite")` with confidence and warnings
- Config via `MNEMOSYNE_WRITE_CLASSIFIER` env var: `off` (default), `warn`, `strict`
- Wired into `Mnemosyne.remember()` at `memory.py:331-340` — ALL entry points (MCP, CLI, Hermes provider, SDK) benefit

**Layer 2: Post-storage hygiene** (`mnemosyne/core/hygiene.py:1-570`)
- `audit_noise(db_path, limit, tables, min_score)` — scans `working_memory` + `memories` tables, scores each row 0.0-1.0 for noise likelihood, returns ranked `NoiseCandidate` list
- `clean_noise(db_path, candidates, action, confirm, dry_run)` — processes audit output with 4 actions: `delete`, `archive`, `flag`, `keep`
- `restore_archived(db_path, log_entry_ids, limit)` — reverses archive operations using preserved `_original_importance` in metadata
- Writes full audit trail to `hygiene_audit_log` table (memory_id, table, action, reason, noise_score, secret_flags, content preview, original metadata, timestamp)
- Noise scoring (`_score_noise` at `hygiene.py:132-201`) uses 9 signals: regex pattern match, secret detection, trivial keywords, terminal markers, stack trace markers, dump heuristics, low importance, value keyword dampening, source-based heuristic
- Archive action is reversible: zeroes importance but preserves `_original_importance` in metadata for exact restore

**Layer 3: Content sanitization** (`mnemosyne/core/content_sanitizer.py:1-169`)
- Detects binary-shaped content (data URIs, base64 blobs, large payloads)
- Extracts to content-addressed blob storage (`~/.hermes/mnemosyne/blobs/<hash>`)
- Uses Shannon entropy (>5.0 bits/char = likely encoded) and size thresholds (1MB hard cap, 100KB base64 check)
- Called from `memory.py:349` before BEAM write

### 1.2 Storage schema and where noise lives

**Working memory** (`beam.py:565-574`):
```sql
CREATE TABLE working_memory (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    source TEXT,
    timestamp TEXT,
    session_id TEXT DEFAULT 'default',
    importance REAL DEFAULT 0.5,
    metadata_json TEXT,
    veracity TEXT DEFAULT 'unknown',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```
Plus: `consolidated_at TEXT` (E3 migration, `beam.py:650`), `scope`, `valid_until`, `superseded_by`, `pinned`, `last_recalled`

**Episodic memory** (`beam.py:583-594`): Same shape + `summary_of TEXT` (links to source WM IDs)

**Hygiene audit log** (`hygiene.py:44-58`): Full audit trail for all cleanup operations

### 1.3 Existing decay and eviction mechanisms

**TTL-based trim** (`beam.py:3687-3701`):
- `DELETE FROM working_memory WHERE session_id = ? AND consolidated_at IS NULL AND (timestamp < ? OR id NOT IN (... LIMIT ?))`
- Default: 168 hours (7 days), 10,000 items max
- Only trims NOT-YET-consolidated rows (E3 additive contract)

**Sleep consolidation** (`beam.py:7884-7944`):
- Consolidates old WM into episodic summaries
- Marks source rows with `consolidated_at = NOW` (does NOT delete — E3 additive)
- Uses local LLM when available, falls back to aaak compression
- `sleep_all_sessions()` (`beam.py:8208`) — maintenance variant for inactive sessions

**Weibull decay** (`weibull.py:28-63`):
- Per-memory-type decay parameters (shape k, scale eta)
- Profiles decay slowly (k=0.3, eta=8760h = ~1 year), events decay fast (k=1.2, eta=168h = ~1 week)
- Used as a boost factor in recall scoring, not for eviction

**Importance weighting**: `importance REAL DEFAULT 0.5` — used in `get_context()` sort order (global first, then importance DESC, then recency)

### 1.4 The `ignore_patterns` mechanism and its limitations

**Provider-level** (`hermes_memory_provider/__init__.py:1307, 1482-1489, 1568-1575`):
- `self._ignore_patterns: List[str]` — regex patterns
- `_should_filter(content)` — `re.search(pattern, content, re.IGNORECASE)` for each pattern
- Applied ONLY in `sync_turn()` for user/assistant conversation content (`__init__.py:2197, 2208`)
- Config: `ignore_patterns` key in config.yaml, or `MNEMOSYNE_IGNORE_PATTERNS` env var

**Core-level** (`filters.py:189-192, 312-371`):
- `_load_ignore_patterns_from_env()` reads `MNEMOSYNE_IGNORE_PATTERNS`
- `should_remember()` combines user patterns with `DEFAULT_NOISE_PATTERNS`
- Applied at `memory.py:336-340` — the catch-all at the root of `remember()`

**Limitations**:
1. **Pre-storage only** — cannot remediate noise already in the DB
2. **Regex-only** — no semantic understanding (a stack trace embedded in a valuable memory would be rejected wholesale)
3. **Provider-level filter only covers sync_turn** — direct `remember()` calls from MCP/CLI bypass the provider filter (though the core filter at `memory.py:336` catches them)
4. **No feedback loop** — filtered content is silently dropped; no log of what was rejected
5. **No per-source patterns** — all sources get the same patterns

### 1.5 Config system integration

**Config keys** (`config.py:178-183`):
- `ignore_patterns` → `MNEMOSYNE_IGNORE_PATTERNS` env var
- `write_classifier` → `MNEMOSYNE_WRITE_CLASSIFIER` env var

**Profile presets** (`profiles.py:76, 145, 190, 235, 280`):
- `balanced` (default): `write_classifier: "off"`, `ignore_patterns: ""`
- `speed`: `write_classifier: "off"`
- `quality`: `write_classifier: "warn"`
- `research`: `write_classifier: "warn"`
- `paranoid`: `write_classifier: "strict"` (implied by profile description)

### 1.6 CLI and MCP surface

**CLI** (`cli.py:711-857`):
- `mnemosyne hygiene audit [--limit N] [--min-score F] [--json]` — scan for noise
- `mnemosyne hygiene clean --action delete|archive|flag [--confirm] [--dry-run] <candidates.json>` — process candidates
- `mnemosyne hygiene restore [--limit N]` — restore archived memories

**MCP** (`mcp_tools.py:927-1037`):
- `mnemosyne_hygiene_audit` — same as CLI audit
- `mnemosyne_hygiene_clean` — same as CLI clean
- Tool schemas at `tool_schemas.py:744-797`

### 1.7 Existing cleanup script

`scripts/cleanup_noisy_mentions.py` — one-off script that removes noisy entity annotations (ASSISTANT, USER, SKILL, etc.) from the `annotations` table. Predates the hygiene system. Has `--dry-run` and `--db` flags, creates backups.

---

## 2. Gap Analysis: What Exists vs. What Is Needed

### 2.1 What exists (working)

| Capability | Status | Location |
|---|---|---|
| Pre-storage noise filtering (regex) | ✅ Shipped | `filters.py:240-371`, wired at `memory.py:336` |
| Pre-storage secret detection | ✅ Shipped | `filters.py:225-237` |
| Post-storage noise audit | ✅ Shipped | `hygiene.py:246-330` |
| Post-storage cleanup (delete/archive/flag) | ✅ Shipped | `hygiene.py:337-483` |
| Reversibility (restore archived) | ✅ Shipped | `hygiene.py:490-570` |
| Audit trail | ✅ Shipped | `hygiene.py:44-58` (`hygiene_audit_log` table) |
| Content sanitization (binary blobs) | ✅ Shipped | `content_sanitizer.py:103-169` |
| CLI surface | ✅ Shipped | `cli.py:711-857` |
| MCP tool surface | ✅ Shipped | `mcp_tools.py:927-1037` |
| TTL-based eviction | ✅ Shipped | `beam.py:3687-3701` |
| Sleep consolidation | ✅ Shipped | `beam.py:7884-7944` |
| Weibull decay scoring | ✅ Shipped | `weibull.py:28-63` |

### 2.2 What is missing (the gaps)

#### Gap 1: No semantic noise detection (regex-only)
The current `_score_noise()` function in `hygiene.py:132-201` is purely deterministic regex + structural heuristics. It cannot catch:
- Conversational noise that doesn't match patterns ("ok", "got it" in context)
- Low-value memories that are syntactically valid but semantically empty
- Duplicate or near-duplicate memories (the same fact stored 20 times)
- Outdated memories that have been superseded but not formally invalidated

**Impact:** Regex catches ~60-70% of noise. The remaining 30% requires semantic understanding.

#### Gap 2: No batch/pagination for large databases
`audit_noise()` has a `limit` parameter (default 200) but no pagination. For a database with 50,000+ rows, the user must manually run `audit --limit 50000` or run multiple passes. There's no cursor-based scanning.

**Impact**: Large databases (the community users who complain about noise) cannot be efficiently audited.

#### Gap 3: No semantic deduplication
The SHMR module (`shmr.py:356`) can cluster similar memories by embedding similarity, but it's not wired into the hygiene pipeline. A user with 50 copies of "User prefers concise responses" has no way to detect and collapse duplicates.

**Impact**: Context bloat from duplicate memories degrades retrieval quality.

#### Gap 4: No noise summary/dashboard
`diagnose.py` has orphan diagnostics (`_memory_orphan_diagnostics` at `diagnose.py:65`) but no noise summary. A user cannot run `mnemosyne diagnose` and see "47% of your working memory appears to be noise."

**Impact**: Users don't know they have a noise problem until it degrades retrieval.

#### Gap 5: Hygiene not integrated into sleep/consolidation
`sleep()` consolidates old WM into episodic summaries, but it doesn't filter noise during consolidation. A noisy memory gets summarized alongside valuable ones, polluting the episodic summary.

**Impact**: Noise propagates from working memory into episodic memory, where it's harder to remove.

#### Gap 6: No automated/scheduled hygiene
Hygiene is manual-only. There's no `auto_hygiene` config option or cron-style maintenance that runs `audit_noise()` + `clean_noise()` on a schedule.

**Impact**: Noise accumulates between manual cleanup passes.

#### Gap 7: `episodic_memory` table not scanned by default
`audit_noise()` defaults to `["working_memory", "memories"]` (`hygiene.py:267`). The `episodic_memory` table — where consolidated summaries live — is not scanned.

**Impact**: Noise that propagated into episodic summaries is invisible to the audit.

#### Gap 8: No "noise score" column on memories
The noise score is computed during audit and stored in the audit log, but not persisted on the memory row itself. There's no way to filter recall by noise score.

**Impact**: Even after auditing, noisy memories still compete equally in retrieval.

#### Gap 9: Profile defaults don't enable write classifier
All profiles ship with `write_classifier: "off"` or `"warn"` — none enable `"strict"` by default. New users get no noise prevention until they manually configure it.

**Impact**: New databases accumulate noise from day one.

#### Gap 10: No feedback loop for filtered content
When `should_remember()` returns `False`, the content is silently dropped (`memory.py:339-340`). No log, no counter, no way for the user to see what was filtered.

**Impact**: Users can't verify their ignore_patterns are working correctly.

---

## 3. Proposed Solutions

### Solution A: Extend the Existing Hygiene Framework (Incremental)

**Philosophy**: The framework is 80% built. Close the gaps without adding new architectural surface.

**Changes**:

1. **Add pagination to `audit_noise()`** — cursor-based scanning for large DBs
   - Add `offset` parameter, loop internally in batches of 1000
   - Add `--all` flag to CLI that scans everything
   - Estimated: ~50 lines in `hygiene.py`, ~10 in `cli.py`

2. **Scan `episodic_memory` by default** — add to default tables list
   - `hygiene.py:267`: `tables = ["working_memory", "memories", "episodic_memory"]`
   - Estimated: 1 line change + testing

3. **Integrate hygiene into sleep()** — pre-filter noise before consolidation
   - In `beam.py:7920-7928` (sleep SELECT), add `AND id NOT IN (SELECT memory_id FROM hygiene_audit_log WHERE action IN ('deleted', 'archived'))`
   - Or: run `_score_noise()` on each row during sleep, skip high-noise rows from summarization
   - Estimated: ~30 lines in `beam.py`

4. **Add noise diagnostics to `diagnose.py`** — noise summary in health check
   - New `_noise_diagnostics(conn)` function that runs a lightweight `COUNT` query with noise pattern matching
   - Report: "Working memory: 4,231 rows, ~612 likely noise (14.5%)"
   - Estimated: ~60 lines in `diagnose.py`

5. **Enable `write_classifier: "warn"` in balanced profile** — flip the default
   - `profiles.py:145`: change `"write_classifier": "off"` to `"write_classifier": "warn"`
   - Estimated: 1 line, but requires CHANGELOG and migration note

6. **Add filtered-content logging** — track what's being rejected
   - In `memory.py:339`, add a debug counter and optional log table
   - New `filtered_writes` table: `(timestamp, content_preview, reason, pattern_hit)`
   - Estimated: ~40 lines

7. **Wire SHMR into hygiene for dedup** — `hygiene dedup` subcommand
   - New CLI subcommand: `mnemosyne hygiene dedup [--similarity 0.88] [--dry-run]`
   - Uses SHMR clustering (`shmr.py:356`) to find near-duplicates
   - Suggests merge/consolidation actions
   - Estimated: ~100 lines new code in `hygiene.py`, ~20 in `cli.py`

**Pros**:
- Minimal new architectural surface
- Builds on tested, reviewed code
- Each gap closure is independently shippable
- Backward compatible

**Cons**:
- Still regex-only for noise detection (no semantic understanding)
- Doesn't solve the "semantically empty but syntactically valid" problem
- Incremental — won't feel like a step-change to community

### Solution B: Semantic Noise Scoring (LLM-Augmented)

**Philosophy**: Use the local LLM (already integrated for consolidation) to semantically classify memories as noise vs. signal.

**Changes**:

1. **New `semantic_noise_score()` in `hygiene.py`**
   - Takes a memory content string, returns (score, reason)
   - Uses `local_llm.py` (already in the codebase at `mnemosyne/core/local_llm.py`)
   - Prompt: "Rate this memory's long-term value for an AI agent (0.0-1.0). Consider: Is it transient? Is it a command/output dump? Does it contain reusable knowledge? Respond with JSON: {score, reason}"
   - Batch mode: send 10-20 memories per LLM call to reduce overhead
   - Falls back to regex `_score_noise()` when LLM unavailable

2. **Two-phase audit**: `audit_noise()` runs regex first, then optionally runs LLM on borderline cases (0.3 < score < 0.7)
   - Borderline cases are where semantic understanding adds the most value
   - Config: `MNEMOSYNE_SEMANTIC_HYGIENE=1` to enable

3. **Dedup detection via embeddings**
   - Use existing `memory_embeddings` table (or `vec_working` for binary vectors)
   - For each memory, find nearest neighbors above cosine 0.88
   - Group into clusters, suggest consolidation

4. **Noise score persistence**
   - Add `noise_score REAL` column to `working_memory` and `memories`
   - `audit_noise()` writes scores back to rows
   - `get_context()` and recall use noise_score as a ranking penalty

**Pros**:
- Catches the 30% of noise that regex misses
- Dedup via embeddings is architecturally sound (the infrastructure exists)
- Noise score on the row enables retrieval-time filtering
- Uses the local LLM that's already integrated

**Cons**:
- LLM dependency — not available in all deployments (core principle: no LLM required for core functionality)
- Latency — auditing 10,000 rows with LLM takes minutes
- Cost — even local LLM inference has compute cost
- Schema migration (new column) — needs careful migration
- Over-engineering risk — may over-classify valuable memories as noise

### Solution C: Tiered Auto-Hygiene Pipeline (Architectural)

**Philosophy**: Make noise remediation automatic, scheduled, and tiered — not a manual afterthought.

**Changes**:

1. **New `auto_hygiene` config option** (like `auto_sleep`)
   - `MNEMOSYNE_AUTO_HYGIENE=1` — enables automatic noise auditing
   - Runs as part of the sleep cycle (or after it)
   - Configurable thresholds: `auto_hygiene_min_score`, `auto_hygiene_action` (default: "archive")

2. **Three-tier audit pipeline**:
   ```
   Tier 1: Regex patterns (fast, deterministic) — runs on every sleep cycle
   Tier 2: Structural heuristics (medium, deterministic) — runs on every Nth sleep cycle
   Tier 3: Semantic scoring (slow, LLM) — runs only when LLM available, on borderline cases
   ```

3. **Automatic archival** — high-confidence noise (score >= 0.9) is auto-archived
   - Low-confidence noise (0.5 <= score < 0.9) is flagged for manual review
   - Secrets are always flagged, never auto-deleted
   - All actions logged to `hygiene_audit_log`

4. **Integration with consolidation**:
   - Before sleep consolidation, run Tier 1 audit on eligible rows
   - Noisy rows are archived (importance=0) instead of summarized
   - This prevents noise from polluting episodic summaries

5. **Noise-aware recall**:
   - Add `noise_score` to recall ranking: `final_score = base_score * (1.0 - noise_score * 0.5)`
   - Archived memories (importance=0) already excluded from `get_context()`
   - Optionally exclude flagged memories from recall entirely

6. **Dashboard** — `mnemosyne hygiene status`
   - Shows: total memories, noise ratio, last audit date, archived count, flagged count
   - Pulls from `hygiene_audit_log` aggregate queries

**Pros**:
- Solves the problem end-to-end (prevention + detection + remediation + retrieval)
- Automatic — users don't need to remember to run hygiene
- Tiered — respects the "no LLM required for core" principle
- Integrates with existing sleep cycle (no new daemon)
- Noise-aware recall is the real win — even if noise exists, it stops competing

**Cons**:
- Largest implementation effort (~300-400 lines new code)
- Auto-archival risk — false positives get archived without human review
- Adds complexity to the sleep cycle (which is already sensitive — see #432 write-lock fix)
- Needs extensive testing to avoid data loss
- Schema migration for `noise_score` column

---

## 4. Recommended Next Steps (Prioritized)

### Phase 1: Quick Wins (ship in days)

1. **Add `episodic_memory` to default audit tables** — 1 line change in `hygiene.py:267`
2. **Add pagination to `audit_noise()`** — cursor-based, ~50 lines
3. **Add noise summary to `diagnose.py`** — lightweight COUNT with pattern matching, ~60 lines
4. **Flip `write_classifier` default to `"warn"` in balanced profile** — 1 line + CHANGELOG
5. **Add `hygiene status` CLI subcommand** — aggregate from `hygiene_audit_log`, ~40 lines

### Phase 2: Integration (ship in 1-2 weeks)

6. **Integrate hygiene into sleep cycle** — pre-filter noise before consolidation, ~30 lines in `beam.py`
7. **Add filtered-content logging** — track what `should_remember()` rejects, ~40 lines
8. **Wire SHMR dedup into hygiene** — `hygiene dedup` subcommand, ~120 lines

### Phase 3: Semantic (ship when stable)

9. **Add `semantic_noise_score()` using local LLM** — two-phase audit with LLM on borderline cases, ~100 lines
10. **Add `noise_score` column to memory tables** — schema migration + recall ranking integration
11. **Noise-aware recall** — penalize noisy memories in ranking

### Phase 4: Automation (ship when Phase 1-3 are validated)

12. **`auto_hygiene` config option** — automatic audit + archive on sleep cycle
13. **Dashboard and reporting** — `hygiene status` with trends

---

## 5. Prevention Recommendations

### 5.1 Updated `ignore_patterns` examples for config.yaml

```yaml
# config.yaml — add to any profile
ignore_patterns: |
  # Terminal/shell output
  ^\s*(\$|>|#)\s*(pip|npm|npx|yarn|cargo|brew|apt|dnf|pacman)\s
  ^\s*(Collecting|Downloading|Installing|Building|Successfully installed)
  ^\s*Requirement already satisfied
  ^\s*(added|removed|changed)\s+\d+\s+package
  ^\s*(npm warn|npm error|npm notice)
  ^\s*(total\s+\d+|drwx|-\w+-\w+\s)
  # Heartbeats / cron noise
  ^\[?(heartbeat|ping|pong|alive|ok)\]?$
  ^\s*(tick|tock)\s*$
  ^cron\s+(started|completed|skipped|tick)
  # Stack traces / debug logs
  ^Traceback \(most recent call last\):
  ^\s+File \".+\", line \d+
  ^\s+(raise|return)\s+\w+Error
  ^(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+\d{4}-\d{2}-\d{2}
  ^\s*at\s+.*\(.+:\d+:\d+\)
  # Transient status
  ^(Phase|Step|Stage)\s+\d+\s+(done|complete|started|pending)
  ^(PR|Issue|Commit|Merge)\s*#\d+\s+(fixed|done|merged|closed)
  ^\s*(TODO|FIXME|HACK|XXX)\b
  # Empty/trivial
  ^\s*$
  ^(ok|done|yes|no|sure|thanks|got it)\.?$
  # Secrets (use write_classifier=strict for enforcement)
  (?:sk|pk|rk)-[a-zA-Z0-9]{20,}
  AKIA[0-9A-Z]{16}
  gh[pousr]_[A-Za-z0-9]{36}
  xox[baprs]-[A-Za-z0-9-]+
  AIza[0-9A-Za-z_\-]{35}
  eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+
  -----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----
  (?:postgres|mysql|mongodb|redis)://[^:]+:[^@]+@

write_classifier: warn  # or 'strict' for production
```

### 5.2 Upstream improvements

1. **Enable `write_classifier: "warn"` by default in the balanced profile** — prevents new noise from accumulating without blocking writes
2. **Add a `MNEMOSYNE_FILTERED_LOG=1` env var** — logs filtered content to a dedicated table for auditing filter effectiveness
3. **Document the hygiene system** — the CLI and MCP tools exist but are not mentioned in README.md or docs/. Add a "Memory Hygiene" section to docs/
4. **Add `ignore_patterns` examples to docs/configuration.md** — the current docs don't show how to configure noise prevention
5. **Consider a `--suggest-patterns` flag for `hygiene audit`** — after auditing, suggest `ignore_patterns` that would have caught the detected noise (closes the feedback loop)

---

## 6. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                     MEMORY INTAKE PATHS                              │
│  Hermes Provider  │  MCP Server  │  CLI  │  SDK  │  Sync  │  Import │
└───────┬───────────┴──────┬───────┴───┬───┴───┬───┴───┬───┴────┬─────┘
        │                   │           │       │       │        │
        ▼                   ▼           ▼       ▼       ▼        ▼
┌───────────────────────────────────────────────────────────────────────┐
│  LAYER 1: PRE-STORAGE FILTER (filters.py)                             │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────────┐  │
│  │ Provider filter │  │ Core classifier  │  │ Content sanitizer  │  │
│  │ _should_filter  │→ │ should_remember  │→ │ sanitize_content   │  │
│  │ (regex only)    │  │ (regex+heuristic)│  │ (binary extraction)│  │
│  └─────────────────┘  └──────────────────┘  └────────────────────┘  │
│                              │                                        │
│                    ┌─────────▼──────────┐                             │
│                    │ WriteDecision      │  allow → write to BEAM     │
│                    │ (allow/reject)     │  reject → drop + log       │
│                    └────────────────────┘                             │
└───────────────────────────────────────────────────────────────────────┘
                               │ allow
                               ▼
┌───────────────────────────────────────────────────────────────────────┐
│  BEAM STORAGE (beam.py)                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐          │
│  │ working_mem  │  │ episodic_mem │  │ memory_embeddings │          │
│  │ (hot, 7-day  │  │ (summaries)  │  │ (vector index)    │          │
│  │  TTL, 10K    │  │              │  │                   │          │
│  │  max)        │  │              │  │                   │          │
│  └──────┬───────┘  └──────┬───────┘  └───────────────────┘          │
│         │                 ▲                                            │
│         │ sleep()         │ consolidate_to_episodic()                │
│         ▼                 │                                            │
│  ┌──────────────┐         │                                            │
│  │ Sleep cycle  │─────────┘                                            │
│  │ (LLM/aaak)   │                                                      │
│  └──────────────┘                                                      │
└───────────────────────────────────────────────────────────────────────┘
        │                                           ▲
        ▼                                           │
┌───────────────────────────────────────────────────────────────────────┐
│  LAYER 2: POST-STORAGE HYGIENE (hygiene.py)                          │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────────┐     │
│  │ audit_noise    │→ │ clean_noise    │→ │ hygiene_audit_log  │     │
│  │ (score 0-1)    │  │ (delete/archive│  │ (full audit trail) │     │
│  │                │  │  /flag/keep)   │  │                    │     │
│  └────────────────┘  └────────────────┘  └────────────────────┘     │
│         │                                                             │
│         │ restore_archived() ←────────────────────────────────────┐  │
│         │                   (reversibility)                       │  │
│  ┌──────▼────────┐                                                 │  │
│  │ CLI: hygiene  │  MCP: mnemosyne_hygiene_audit/clean            │  │
│  │ audit|clean|  │                                                 │  │
│  │ restore       │                                                 │  │
│  └───────────────┘                                                 │  │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────────────┐
│  LAYER 3: RETRIEVAL (polyphonic_recall.py + beam.py)                 │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────────┐     │
│  │ Linear recall  │  │ Polyphonic     │  │ get_context()      │     │
│  │ (FTS5 + WM)    │  │ (hybrid vector │  │ (prompt injection, │     │
│  │                │  │  + keyword)    │  │  excludes archived)│     │
│  └────────────────┘  └────────────────┘  └────────────────────┘     │
│  Ranking: importance × veracity × Weibull recency × embedding sim   │
│  GAP: no noise_score in ranking (proposed in Solution C)            │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 7. Key File Reference

| File | Purpose | Lines |
|---|---|---|
| `mnemosyne/core/filters.py` | Pre-storage write filter, noise/secret patterns | 371 |
| `mnemosyne/core/hygiene.py` | Post-storage audit, cleanup, restore | 570 |
| `mnemosyne/core/content_sanitizer.py` | Binary blob extraction | 169 |
| `mnemosyne/core/beam.py` | Storage, sleep, TTL, consolidation | 8698 |
| `mnemosyne/core/weibull.py` | Per-type temporal decay | ~155 |
| `mnemosyne/core/shmr.py` | Semantic harmonization (dedup candidate) | ~400 |
| `mnemosyne/core/memory.py` | Root remember() with filter gate | 1038 |
| `mnemosyne/core/profiles.py` | Profile presets with filter config | ~400 |
| `mnemosyne/core/config.py` | Config key → env var mapping | ~200 |
| `mnemosyne/cli.py` | CLI surface (hygiene subcommands) | 1077 |
| `mnemosyne/mcp_tools.py` | MCP tool handlers (hygiene_audit/clean) | ~1037 |
| `mnemosyne/tool_schemas.py` | MCP tool schemas | ~800 |
| `mnemosyne/diagnose.py` | Health check (orphan diagnostics, no noise yet) | ~500 |
| `hermes_memory_provider/__init__.py` | Provider-level _should_filter | ~2300 |
| `scripts/cleanup_noisy_mentions.py` | Legacy one-off cleanup script | 115 |

---

## 8. Conclusion

The noise remediation framework is **80% built**. The three-layer system (pre-storage filter → post-storage hygiene → content sanitization) is architecturally sound and already wired into all entry points. The gaps are:

1. **Coverage gaps** — episodic_memory not scanned, no pagination for large DBs, no noise in diagnostics
2. **Integration gaps** — hygiene not wired into sleep, no auto-hygiene, no noise-aware recall
3. **Semantic gaps** — regex-only detection, no dedup, no semantic scoring
4. **Discoverability gaps** — not documented, profile defaults don't enable prevention

The recommended path is **Solution A (incremental) for Phase 1-2**, closing coverage and integration gaps on the existing framework. **Solution C (auto-hygiene)** for Phase 3-4, adding automation and noise-aware recall. **Solution B (semantic)** only when the local LLM is available and the community validates that regex is insufficient.

The key insight: noise-aware recall (`get_context()` and `polyphonic_recall` penalizing noisy memories) is the highest-impact change. Even if noise exists in the DB, it stops degrading retrieval when it's scored and penalized in ranking.
