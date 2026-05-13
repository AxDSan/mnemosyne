# Mnemosyne — Session Continuation Reference

## Branch
`fix/mention-entity-extraction-quality` (fork: ether-btc/mnemosyne)

## PR
**Open**: https://github.com/AxDSan/mnemosyne/pull/120

## What was done
**Problem**: 179 episodic memories → 7,591 mentions (42x amplification).
Noise top entries: ASSISTANT(394), USER(375), SKILL(104), Review(104),
TARGET(93), CLASS(91), LEVEL(86), SIGNALS(86).

**Fix in `mnemosyne/core/entities.py`**:
1. Expanded `ENTITY_EXTRACTION_STOP_WORDS` with 25+ meta/system words
2. Made stopword filter case-insensitive (`.lower()`)
3. Added any-word-stopword filter (drops entities if ANY word is a stopword)

**Changed files**: `mnemosyne/core/entities.py` only

## Pending work (not yet implemented)
1. **Post-merge cleanup**: After PR is merged, backport the stopword fix to the existing 7,591 noisy mentions in the DB:
   ```sql
   DELETE FROM annotations WHERE kind='mentions' AND value IN (
     'ASSISTANT','USER','SKILL','Review','Target','CLASS','Level',
     'Signals','Phase','API','Pi','Summary','Added','Active','Be',
     'Not','Whether','All','No','Replying','AI','Memory','Mnemosyne',
     'Conversation','Fact','Signal','Hermes','Agent','Model','System',
     'Note','Task','Project','Result','Output','Input','Data','Step',
     'Process','Point','Way','Thing','Time','Work'
   );
   ```
2. **DB backup** before cleanup: `mnemosyne.db.pre_stopword_cleanup`
3. **Trust tier filtering** (mentioned in original analysis): skip entity extraction on content where `trust_tier != 'STATED'`

## Quick test
```bash
cd /home/hermes-pi/mnemosyne
python3 -c "from mnemosyne.core.entities import extract_entities_regex; \
  print(extract_entities_regex('The USER should review the SKILL')); \
  print(extract_entities_regex('Alice and Bob met in New York'))"
# Expected: [] and ['Alice', 'Bob', 'New York']
```

## Git state
- Local branch: `fix/mention-entity-extraction-quality`
- Pushed to: `fork/fix/mention-entity-extraction-quality`
- 1 commit ahead of `origin/main` (ab0e0ed)
- PR target: `AxDSan/mnemosyne` main branch
