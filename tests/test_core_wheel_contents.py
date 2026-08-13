"""Regression coverage for the installable Core wheel payload."""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _build_core_wheel(tmp_path: Path) -> Path:
    build_root = tmp_path / "core"
    shutil.copytree(
        ROOT,
        build_root,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            ".codex",
            ".codegraph",
            "openspec",
            "build",
            "dist",
            "*.egg-info",
            "__pycache__",
        ),
    )
    wheel_dir = tmp_path / "wheels"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
            ".",
        ],
        cwd=build_root,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected one Core wheel, found {wheels}"
    return wheels[0]


def test_core_wheel_excludes_repository_only_hermes_sources(tmp_path):
    wheel = _build_core_wheel(tmp_path)

    with zipfile.ZipFile(wheel) as archive:
        members = archive.namelist()

    assert "mnemosyne/__init__.py" in members
    leaked = [path for path in members if path.startswith("integrations/hermes/")]
    assert not leaked, f"Core wheel contains repository-only Hermes payload: {leaked}"
