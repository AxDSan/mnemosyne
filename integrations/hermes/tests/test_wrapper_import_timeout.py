"""Focused coverage for wrapper validation timeout configuration."""

import subprocess
import sys
from pathlib import Path

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


def test_wrapper_validation_default_timeout_reaches_both_probes(tmp_path, monkeypatch):
    observed_timeouts = []

    def successful_probe(command, **kwargs):
        observed_timeouts.append(kwargs["timeout"])
        if "-S" in command:
            return subprocess.CompletedProcess(command, 0, "0.0-test\n", "")
        return subprocess.CompletedProcess(command, 0, f"{tmp_path}\n", "")

    monkeypatch.setattr(install.subprocess, "run", successful_probe)

    python, site_packages = install._validated_wrapper_environment(Path(sys.executable))

    assert python == Path(sys.executable)
    assert site_packages == tmp_path
    assert observed_timeouts == [60.0, 60.0]


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
