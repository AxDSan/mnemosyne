"""Prevent static Hermes plugin manifests from drifting from their packages."""

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _assignment_version(path: Path) -> str:
    match = re.search(
        r'^__version__\s*=\s*["\']([^"\']+)["\']',
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match, f"missing __version__ assignment in {path}"
    return match.group(1)


def _manifest_version(path: Path) -> str:
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(manifest, dict), f"invalid plugin manifest in {path}"
    version = manifest.get("version")
    assert isinstance(version, str), f"missing manifest version in {path}"
    return version


def _source_manifest_paths() -> set[Path]:
    generated_roots = {".git", ".venv", "venv", "env", "build", "dist"}
    return {
        path
        for path in ROOT.rglob("plugin.yaml")
        if not any(
            part in generated_roots or part.endswith(".egg-info")
            for part in path.relative_to(ROOT).parts
        )
    }


def test_all_plugin_manifests_have_an_explicit_version_contract():
    core_version = _assignment_version(ROOT / "mnemosyne" / "__init__.py")
    hermes_root = ROOT / "integrations" / "hermes"
    hermes_version = _assignment_version(
        hermes_root / "src" / "mnemosyne_hermes" / "__init__.py"
    )
    expected_versions = {
        ROOT / "hermes_memory_provider" / "plugin.yaml": core_version,
        hermes_root / "plugin.yaml": hermes_version,
        hermes_root / "src" / "mnemosyne_hermes" / "plugin.yaml": hermes_version,
    }

    assert _source_manifest_paths() == set(expected_versions)
    for manifest_path, expected_version in expected_versions.items():
        assert _manifest_version(manifest_path) == expected_version


def test_package_metadata_uses_the_same_version_contract():
    core_project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(
        r'^version\s*=\s*\{attr\s*=\s*"mnemosyne\.__version__"\}$',
        core_project,
        re.MULTILINE,
    )

    hermes_project = (
        ROOT / "integrations" / "hermes" / "pyproject.toml"
    ).read_text(encoding="utf-8")
    hermes_version = _assignment_version(
        ROOT / "integrations" / "hermes" / "src" / "mnemosyne_hermes" / "__init__.py"
    )
    assert re.search(
        rf'^version\s*=\s*"{re.escape(hermes_version)}"$',
        hermes_project,
        re.MULTILINE,
    )
