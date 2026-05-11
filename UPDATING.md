# Updating Mnemosyne

Mnemosyne can be installed from **PyPI** or from **source**. This guide covers both paths.

---

## Quick Reference

| What changed | User action |
|---|---|
| PyPI update (new version) | `pip install --upgrade mnemosyne-memory` + restart Hermes |
| Pure Python fix/feature (source) | `git pull` + restart Hermes |
| New dependency / entry point (source) | `git pull` + `pip install -e .` + restart Hermes |
| New CLI command (source) | `git pull` + `pip install -e .` + restart Hermes |
| Database schema (legacy ephemeral → persisted) | `git pull` + `migrate_from_legacy.py` + restart Hermes |
| Database schema (TripleStore split, E6) | `git pull` + restart Hermes (auto-migrates on first BeamMemory init; backup at `{db}.pre_e6_backup`) — or `MNEMOSYNE_AUTO_MIGRATE=0` + `python scripts/migrate_triplestore_split.py` for explicit control |
| `plugin.yaml` / tool schema | Restart Hermes only |

### Upgrading to the E6 TripleStore split

The first BeamMemory init on a pre-E6 database moves annotation-flavored rows (`mentions`, `fact`, `occurred_on`, `has_source`) from the legacy `triples` table to the new `annotations` table. Before any writes, a file-level backup is created at `{db}.pre_e6_backup` (existing backups are not overwritten).

**Default behavior (recommended for most users):**

```bash
pip install --upgrade mnemosyne-memory   # or git pull
hermes gateway restart
# First memory operation auto-migrates; check logs for:
#   "E6: auto-migrated N annotation rows from triples → annotations.
#    Backup written to /path/to/mnemosyne.db.pre_e6_backup."
```

**Manual control (for operators who prefer explicit migrations):**

```bash
# Disable auto-migration
export MNEMOSYNE_AUTO_MIGRATE=0

# Restart Hermes — BeamMemory will log a WARNING listing pending row count
hermes gateway restart

# Run the migration when ready
python scripts/migrate_triplestore_split.py --dry-run    # preview
python scripts/migrate_triplestore_split.py              # commit
```

**What's preserved, what's not:**

| Data | After E6 |
|---|---|
| Existing entity mentions (`mentions`) | Moved to `annotations`. Multiple mentions per memory now coexist (silent-destruction fix). |
| Existing extracted facts (`fact`) | Moved to `annotations`. Multiple facts per memory now coexist. |
| Existing `occurred_on` / `has_source` rows | Moved to `annotations`. |
| Other predicates in `triples` (custom / current-truth) | Stay in `triples`. The table retains auto-invalidation semantics for genuine current-truth facts. |
| Backup file at `{db}.pre_e6_backup` | Always written before mutation; restore by `cp` if anything goes wrong. |

**Rollback:**

```bash
cp ~/.hermes/mnemosyne/data/mnemosyne.db.pre_e6_backup ~/.hermes/mnemosyne/data/mnemosyne.db
# Then pin to a pre-E6 version
pip install 'mnemosyne-memory<X.Y'   # X.Y is the first release including E6
```

---

## By Install Path

### Option A: PyPI (recommended for users)

```bash
pip install --upgrade mnemosyne-memory
hermes gateway restart
```

To verify the new version:

```bash
hermes mnemosyne version
hermes mnemosyne stats --global
hermes memory status
```

### Option B: Full install from source (`pip install -e .`)

For most updates, only `git pull` is required because the editable install symlinks the source:

```bash
cd mnemosyne
git pull
hermes gateway restart
```

**Re-run `pip install -e .` only when:**
- `setup.py` or `pyproject.toml` added new dependencies
- New `entry_points` or console scripts were added
- Package metadata changed

```bash
git pull
pip install -e ".[all,dev]"
hermes gateway restart
```

**Re-run the installer only when** `mnemosyne/install.py` or the symlink logic changed:

```bash
git pull
python -m mnemosyne.install
hermes gateway restart
```

### Option C: Hermes MemoryProvider only (deploy script)

This path symlinks `~/.hermes/plugins/mnemosyne` directly into the repo, so code changes are immediate:

```bash
cd mnemosyne
git pull
hermes gateway restart
```

No `pip install` is ever needed for this path because nothing is installed into a Python environment.

---

## Database Migrations

Mnemosyne uses SQLite with `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`, so **most schema changes upgrade automatically** on the next run. No user action required.

Run the migration script only when:
- The update mentions a database schema change
- You are upgrading from a pre-1.0 version
- You see errors about missing columns or tables

```bash
# Preview first
python scripts/migrate_from_legacy.py --dry-run

# Apply
python scripts/migrate_from_legacy.py
```

The migration script is idempotent — safe to run multiple times.

---

## Rollback

If an update breaks something, roll back to the last known good version:

```bash
# Check out the previous version
git log --oneline -5
git checkout <previous-commit-or-tag>

# If you changed setup.py, re-install
pip install -e .

# Restart Hermes
hermes gateway restart
```

If you exported a backup before updating, restore it:

```bash
hermes mnemosyne import --input mnemosyne_backup.json
```

Or copy the SQLite file directly:

```bash
cp ~/backups/mnemosyne_20260101.db ~/.hermes/mnemosyne/data/mnemosyne.db
```

---

## Verifying an Update

After updating, confirm the new version is active:

```bash
hermes mnemosyne version
hermes mnemosyne stats
hermes mnemosyne stats --global
hermes memory status
```

Check that tools are registered:

```bash
hermes tools list | grep mnemosyne
```

---

## Troubleshooting

### "Command not found" after update

You added a new CLI command but didn't re-run `pip install -e .`. Entry points are registered at install time, not at runtime.

```bash
pip install -e .
```

### "No module named mnemosyne" after update

Your virtual environment may have been deactivated or the editable install broke. Re-install:

```bash
pip install -e .
```

### Plugin changes not taking effect

Hermes caches plugins at startup. You **must** restart:

```bash
hermes gateway restart
```

### Database errors after schema change

Run the migration script:

```bash
python scripts/migrate_from_legacy.py
```

If errors persist, export your data, delete the database, and re-import:

```bash
hermes mnemosyne export --output backup.json
rm ~/.hermes/mnemosyne/data/mnemosyne.db
hermes mnemosyne import --input backup.json
```
