"""Regression tests for the staged-diff PII check in the pre-commit hook."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".githooks" / "pre-commit"
PII = "1641797+AxDSan" + "@users.noreply.github.com"


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def _hook_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "hook-repo"
    (repo / ".githooks").mkdir(parents=True)
    shutil.copy2(HOOK, repo / ".githooks" / "pre-commit")
    (repo / "pyproject.toml").write_text(
        f'[project]\nauthors = [{{name = "Maintainer", email = "{PII}"}}]\n',
        encoding="utf-8",
    )
    for args in (
        ("init", "-q"),
        ("config", "user.email", "hook-test@example.test"),
        ("config", "user.name", "Hook Test"),
        ("add", "."),
        ("commit", "-qm", "fixture"),
    ):
        result = _run(repo, *args)
        assert result.returncode == 0, result.stderr
    return repo


def _run_hook(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", ".githooks/pre-commit"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def test_precommit_allows_harmless_pyproject_change_with_historical_pii(tmp_path):
    repo = _hook_repo(tmp_path)
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text(encoding="utf-8") + 'version = "1.0.0"\n', encoding="utf-8")
    assert _run(repo, "add", "pyproject.toml").returncode == 0

    result = _run_hook(repo)

    assert result.returncode == 0, result.stdout + result.stderr


def test_precommit_rejects_newly_added_pii(tmp_path):
    repo = _hook_repo(tmp_path)
    (repo / "new-contact.txt").write_text(f"contact: {PII}\n", encoding="utf-8")
    assert _run(repo, "add", "new-contact.txt").returncode == 0

    result = _run_hook(repo)

    assert result.returncode != 0
    assert "ERROR: PII detected in staged files." in result.stdout


def test_precommit_rejects_added_pii_with_diff_header_prefix(tmp_path):
    repo = _hook_repo(tmp_path)
    (repo / "header-prefix.txt").write_text(f"+++{PII}\n", encoding="utf-8")
    assert _run(repo, "add", "header-prefix.txt").returncode == 0

    result = _run_hook(repo)

    assert result.returncode != 0
    assert "ERROR: PII detected in staged files." in result.stdout


def test_precommit_rejects_pii_in_staged_binary_content(tmp_path):
    repo = _hook_repo(tmp_path)
    (repo / "contact.bin").write_bytes(b"\x00binary\xff" + PII.encode() + b"\x00")
    assert _run(repo, "add", "contact.bin").returncode == 0

    result = _run_hook(repo)

    assert result.returncode != 0
    assert "ERROR: PII detected in staged files." in result.stdout


def test_precommit_ignores_removed_historical_pii(tmp_path):
    repo = _hook_repo(tmp_path)
    pyproject = repo / "pyproject.toml"
    pyproject.write_text('[project]\nauthors = []\n', encoding="utf-8")
    assert _run(repo, "add", "pyproject.toml").returncode == 0

    result = _run_hook(repo)

    assert result.returncode == 0, result.stdout + result.stderr
