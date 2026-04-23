# PR #11 DevOps Plan: Session-scoped queries ignore `scope='global'`

**PR:** https://github.com/AxDSan/mnemosyne/pull/11  
**Author:** rakaarwaky  
**Status:** Under review  
**Target Version:** v1.10.1 (hotfix)  

---

## 1. Bug Validation Summary

| Bug | Claim | Status | Evidence |
|-----|-------|--------|----------|
| 1 | `get_working_stats()` filters by `session_id` only, hiding global memories | **CONFIRMED** | beam.py:538-540 |
| 2 | `recall()` UPDATE tracking skips global memories | **CONFIRMED** | beam.py:803 |
| 3 | `init_beam()` column defaults to `'session'` instead of `'global'` | **CONFIRMED** | beam.py:253,256 |

**Our v1.10.0 overlap:** Added `get_global_working_stats()` which is complementary, not conflicting.
The PR changes `get_working_stats()` default behavior. We merge both.

---

## 2. Merge Strategy

### Step 1: Fetch PR commits
```bash
git remote add rakaarwaky https://github.com/rakaarwaky/mnemosyne.git
git fetch rakaarwaky main
git cherry-pick 345b432 a7afc28
```

### Step 2: Resolve conflicts
- `get_working_stats()`: PR removes session filter. Our v1.10.0 added `get_global_working_stats()` below it.
  - **Resolution:** Accept PR's change to `get_working_stats()` (global). Keep `get_global_working_stats()` as deprecated alias.

### Step 3: Additional fixes not in PR
- `_find_duplicate()`: Session-only dedupe is likely intentional. No action.
- `remember() UPDATE`: Same. No action.

### Step 4: Test
```bash
python -m pytest tests/test_beam.py -v
python -m pytest tests/test_beam.py::test_global_memory_recall -v
```

### Step 5: Version bump + docs
- Bump `__version__` to `1.10.1`
- Add CHANGELOG entry
- Update README if needed

### Step 6: Push + merge
```bash
git push origin main
gh pr merge 11 --merge --body "Merged with authorship preserved. Added get_global_working_stats() alias."
```

---

## 3. Authorship Preservation

- All commits authored by `rakaarwaky`
- Reviewer/co-author: `AxDSan` (you)
- Use `git commit --amend --author="..."` if manual resolution needed
- Final merge commit should reference PR #11 and preserve original commits in history

---

## 4. Testing Checklist

- [ ] `hermes mnemosyne stats` shows global working memory count
- [ ] Global memory recall_count increments after `recall()`
- [ ] Fresh DB init creates columns with DEFAULT 'global'
- [ ] Existing DB migration applies scope='global' to legacy rows
- [ ] Session-scoped memories still work correctly
- [ ] All existing tests pass

---

## 5. Rollback Plan

If issues found post-merge:
```bash
git revert HEAD~2..HEAD
git push origin main
pip install --upgrade mnemosyne-memory  # if PyPI published
```
