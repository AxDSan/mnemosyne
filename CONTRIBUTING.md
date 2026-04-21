# Contributing to Mnemosyne

Mnemosyne is a personal project that grew into something useful. If you're here, you're already part of the community. There are no gatekeepers — bug reports, documentation fixes, feature ideas, and code contributions are all welcome.

## Getting Started

```bash
git clone https://github.com/AxDSan/mnemosyne.git
cd mnemosyne
pip install -e .
python -m pytest tests/ -v
```

## What You Can Do

**No contribution is too small.**

- **Report bugs** — Open an issue with steps to reproduce. A clear bug report saves hours.
- **Improve docs** — Typos, unclear explanations, missing examples. If it confused you, fix it.
- **Share your use case** — Open a discussion. Real-world usage shapes the roadmap.
- **Submit code** — See below for guidelines.

## Code Contributions

### Principles

Mnemosyne is intentionally minimal. Every addition is weighed against these principles:

- **Local-first:** No cloud dependencies, no required API keys.
- **Minimal dependencies:** Prefer the Python stdlib. SQLite is the only database.
- **Zero-config:** Users should not need to edit config files to get basic functionality.
- **Fast:** Sub-millisecond reads and writes on standard hardware.

### Before You Code

1. **Open an issue first** for non-trivial changes. This prevents wasted effort.
2. **Keep it focused.** One PR per logical change.
3. **Add tests.** If you fix a bug or add a feature, include a test in `tests/`.
4. **Update the README** if user-facing behavior changes.

### Review Process

There is no formal review board. Pull requests are reviewed by the maintainer and merged when they:

- Pass existing tests
- Follow the principles above
- Include a clear description of what changed and why

## Areas of Interest

These are not mandates — just directions where help would be valuable:

- Encrypted backup/sync (optional, user-controlled)
- Additional embedding model support
- Multi-language memory processing
- Better error messages and debugging tools

## Community

- **Issues & bugs:** [GitHub Issues](https://github.com/AxDSan/mnemosyne/issues)
- **Feature ideas & questions:** [GitHub Discussions](https://github.com/AxDSan/mnemosyne/discussions)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
