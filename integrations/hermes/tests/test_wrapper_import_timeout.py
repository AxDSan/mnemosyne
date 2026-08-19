"""Focused coverage for wrapper validation timeout configuration."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mnemosyne_hermes import install


def test_install_cli_passes_default_and_override_import_timeout(tmp_path, monkeypatch):
    received = []
    monkeypatch.setattr(
        install,
        "run_install",
        lambda **kwargs: received.append(kwargs) or 0,
    )

    assert install.main(["--hermes-home", str(tmp_path), "install"]) == 0
    assert install.main(
        ["--hermes-home", str(tmp_path), "install", "--import-timeout", "75"]
    ) == 0

    assert [call["import_timeout"] for call in received] == [60.0, 75.0]


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
def test_install_cli_rejects_non_positive_or_nonfinite_import_timeout(value, capsys):
    with pytest.raises(SystemExit) as exc_info:
        install.main(["install", f"--import-timeout={value}"])

    assert exc_info.value.code == 2
    assert "positive finite number" in capsys.readouterr().err


@pytest.mark.parametrize("import_timeout", [60.0, 90.0])
def test_wrapper_validation_timeout_reaches_both_probes(tmp_path, monkeypatch, import_timeout):
    observed_timeouts = []

    def successful_probe(command, **kwargs):
        observed_timeouts.append(kwargs["timeout"])
        if "-S" in command:
            return subprocess.CompletedProcess(command, 0, "0.0-test\n", "")
        return subprocess.CompletedProcess(command, 0, f"{tmp_path}\n", "")

    monkeypatch.setattr(install.subprocess, "run", successful_probe)

    python, site_packages = install._validated_wrapper_environment(
        Path(sys.executable), import_timeout=import_timeout
    )

    assert python == Path(sys.executable)
    assert site_packages == tmp_path
    assert observed_timeouts == [import_timeout, import_timeout]


def test_plugin_state_accepts_11_second_healthy_wrapper_import(tmp_path, monkeypatch):
    """Status must share the 60-second wrapper-validation policy with install."""
    target = tmp_path / "plugins" / "mnemosyne"
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    install._write_wrapper_plugin(target, python=Path(sys.executable), site_packages=site_packages)
    observed_timeouts = []

    def slow_but_healthy_import(command, **kwargs):
        timeout = kwargs["timeout"]
        observed_timeouts.append(timeout)
        if timeout < 11:
            raise subprocess.TimeoutExpired(command, timeout)
        return subprocess.CompletedProcess(command, 0, "0.0-test\n", "")

    monkeypatch.setattr(install.subprocess, "run", slow_but_healthy_import)

    state = install.plugin_state(hermes_home_path=tmp_path)

    assert observed_timeouts == [60.0]
    assert state.status == "installed"
    assert state.installed is True
    assert state.wrapper_import_ok is True


def test_wrapper_install_timeout_preserves_existing_target(tmp_path, monkeypatch):
    target = tmp_path / "plugins" / "mnemosyne"
    target.mkdir(parents=True)
    sentinel = target / "keep"
    sentinel.write_text("existing wrapper", encoding="utf-8")
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()

    def probe_with_slow_import(command, **kwargs):
        if "-S" in command:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return subprocess.CompletedProcess(command, 0, f"{site_packages}\n", "")

    monkeypatch.setattr(install.subprocess, "run", probe_with_slow_import)

    with pytest.raises(RuntimeError, match="wrapper validation timed out"):
        install.install_plugin(
            hermes_home_path=tmp_path,
            force=True,
            mode="wrapper",
            python=sys.executable,
            link_profiles=False,
        )

    assert sentinel.read_text(encoding="utf-8") == "existing wrapper"


def test_wrapper_import_timeout_diagnostic_names_interpreter_duration_and_retry(tmp_path, monkeypatch):
    def timed_out(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(install.subprocess, "run", timed_out)

    ok, error, invalid_runtime = install._check_wrapper_import(
        tmp_path, Path(sys.executable), import_timeout=75
    )

    assert ok is False
    assert invalid_runtime is False
    assert error is not None
    assert str(Path(sys.executable)) in error
    assert "75 seconds" in error
    assert "--import-timeout 150" in error


def test_wrapper_import_timeout_diagnostic_quotes_windows_python_path(monkeypatch):
    python = Path("C:/Program Files/Hermes/venv/python.exe")
    retry_args = [
        "mnemosyne-hermes",
        "install",
        "--mode",
        "wrapper",
        "--python",
        str(python),
        "--import-timeout",
        "120",
    ]
    monkeypatch.setattr(install, "os", SimpleNamespace(name="nt"))

    error = install._timeout_diagnostic(python, 60)

    assert error.endswith(f"Retry with: {subprocess.list2cmdline(retry_args)}")
    assert '"C:/Program Files/Hermes/venv/python.exe"' in error
