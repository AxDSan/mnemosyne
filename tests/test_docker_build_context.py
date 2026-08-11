from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_dockerignore_excludes_local_secrets_and_runtime_data():
    """Source-built images must not capture local secrets or runtime state."""
    patterns = {
        line.strip()
        for line in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    required = {
        ".env",
        ".env.*",
        ".hermes",
        ".mnemosyne",
        "results",
        "[[]bank[]]",
        ".codegraph",
        "*.db",
        "*.db-wal",
        "*.db-shm",
        "backups",
        "logs",
    }

    assert required <= patterns
