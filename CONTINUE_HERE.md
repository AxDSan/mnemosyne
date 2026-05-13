# Mnemosyne — Session Continuation Reference

## Branch
`fix/mention-entity-extraction-quality` (fork: ether-btc/mnemosyne)

## PR
**Open**: https://github.com/AxDSan/mnemosyne/pull/120

## What was done (3 commits)

### Commit 1: `65e23c9` — Write-time entity extraction fix
- Expanded `ENTITY_EXTRACTION_STOP_WORDS` with 40+ meta/system words
- Made stopword filter case-insensitive (`.lower()`)
- Added any-word-stopword filter (drops entities if ANY word is a stopword)
- Changes: `mnemosyne/core/entities.py` only

### Commit 2: `0f0db2c` — Retrieval-time noisy-mention filter
- Added `_is_noisy_mention()` and `filter_clean_mentions()` to `annotations.py`
- Added `filter_noise=True` parameter to `AnnotationStore.query_by_kind()` — filters
  noise at retrieval time instead of requiring destructive SQL DELETE from the DB
- Updates `CONTINUE_HERE.md` with verified test results

### Commit 3: `3bea98e` — Non-destructive defense-in-depth
- Updated test cases to match new any-word-stopword behavior
- Added `scripts/cleanup_noisy_mentions.py` for optional post-merge DB cleanup

## Verification (tested)
Test 1: 'The USER should review the SKILL' → [] ✓
Test 2: 'Alice and Bob met in New York' → ['Alice', 'Bob', 'New York'] ✓
Test 3: 'The assistant told the User about the API' → [] ✓
Test 4: All 49 tests pass ✓

## Pending work (after PR merge)

### 1. Optional: DB cleanup of existing noisy annotations
The retrieval-time filter means the DB can remain as-is and noisy mentions
are automatically excluded from queries. If a full cleanup is desired:

```bash
python3 /home/hermes-pi/mnemosyne/scripts/cleanup_noisy_mentions.py --dry-run
```

### 2. Trust tier filtering (future)
Skip entity extraction on content where `trust_tier != 'STATED'`.

## Git state
- Local branch: `fix/mention-entity-extraction-quality`
- Pushed to: `fork/fix/mention-entity-extraction-quality`
- 6 commits ahead of `origin/main` (ab0e0ed)
- All pushed and PR updated
- All 49 tests pass
