# Mnemosyne — Session Continuation Reference

## Branch
`fix/mention-entity-extraction-quality` (fork: ether-btc/mnemosyne)

## PR
**Open**: https://github.com/AxDSan/mnemosyne/pull/120

## What was done
**Problem**: 179 episodic memories → 7,591 mentions (42x amplification).
Noise top entries: ASSISTANT(394), USER(375), SKILL(159), Review(104),
Not(97), Target(93), CLASS(91), LEVEL(86), Signals(86), WHETHER(86).

**Fix in `mnemosyne/core/entities.py`** (4 commits on branch):
1. Expanded `ENTITY_EXTRACTION_STOP_WORDS` with 40+ meta/system words
2. Made stopword filter case-insensitive (`.lower()`)
3. Added any-word-stopword filter (drops entities if ANY word is a stopword)

**Changed files**: `mnemosyne/core/entities.py` only

## Verification (tested)
Test 1: 'The USER should review the SKILL' → [] ✓
Test 2: 'Alice and Bob met in New York' → ['Alice', 'Bob', 'New York'] ✓
Test 3: 'The assistant told the User about the API' → [] ✓
Test 4: 'Hermes Agent deployed on Raspberry Pi' → ['Raspberry'] ✓

## Pending work (requires PR merge first)

### 1. Post-merge DB cleanup — delete noisy annotations
After PR is merged, backport the stopword fix to existing mentions in the DB:

Step 1: Backup
```bash
DB="/home/hermes-pi/.hermes/mnemosyne/data/mnemosyne.db"
BACKUP="${DB}.pre_stopword_cleanup.$(date +%Y%m%d%H%M%S)"
cp "$DB" "$BACKUP"
```

Step 2: Delete noisy annotations
```bash
DB="/home/hermes-pi/.hermes/mnemosyne/data/mnemosyne.db"
python3 /home/hermes-pi/mnemosyne/scripts/cleanup_noisy_mentions.py "$DB"
```

### 2. Trust tier filtering (future)
Skip entity extraction on content where `trust_tier != 'STATED'`.
See `fix/summarize-memories-chunk_source-arg` branch for related work.

## Git state
- Local branch: `fix/mention-entity-extraction-quality` (on fork: ether-btc/mnemosyne)
- Fork: `fork/fix/mention-entity-extraction-quality`
- 4 commits ahead of `origin/main` (ab0e0ed)
- PR target: `AxDSan/mnemosyne` main
