# Changelog

**The changelog lives at [CHANGELOG.md](../CHANGELOG.md) in the repository root.**
That file is the single source of truth, follows
[Keep a Changelog](https://keepachangelog.com/), and is what the website and the
release tooling read.

This page previously carried a hand-maintained "Recent Releases" summary. It fell nine
releases behind, and it ended up contradicting pages that linked to it: this page
correctly attributed the sync feature set to 3.6.0 while the docs site claimed the same
items shipped in 3.12.0. A second copy of a changelog has no way to stay right, so it
is now a pointer.

## Release state

Mnemosyne follows strict SemVer from v3.1.2 onward. See [RELEASING.md](../RELEASING.md)
for the process, which `.githooks/pre-push` enforces.

The in-tree `__version__` can be ahead of the newest published release while work
accumulates toward the next tag, so the version you read in the source is not
necessarily one you can install. To check both:

```bash
# What is published
pip index versions mnemosyne-memory

# What you have
python -c "import mnemosyne; print(mnemosyne.__version__)"
```

A merged fix is not in your CLI until a release ships. If you installed with `pipx`,
note that `pipx reinstall` pulls from PyPI and will silently drop unreleased local
fixes.

## Deeper references

| Topic | Document |
|---|---|
| Sync, introduced in 3.6.0 | [Mnemosyne Sync](sync/index.md) |
| BEAM tiers and the MEMORIA layer | [Architecture](architecture.md) |
| Published benchmark results | [BEAM benchmark](beam-benchmark.md) |
| Upgrade and rollback | [UPDATING.md](../UPDATING.md) |
