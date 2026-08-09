"""Regression tests for Hermes interpreter validation (issue #618, after #620).

#620 taught discovery to follow a shell-wrapper launcher through its ``exec``
line, which fixed the reported symptom. These tests cover what remains: a
candidate is accepted only after it is shown to be a real virtual environment,
the venv symlink is not resolved away, ``--python`` reaches discovery, and a run
with nothing validated stops rather than bootstrapping into a guess.

Two layouts still returned the wrong interpreter with #620 alone:

* a launcher on PATH that is neither a symlink nor an ``exec`` wrapper -- a
  script that calls the real binary as a subprocess, or a compiled shim with no
  ``exec`` line -- resolves to itself, leaving ``bin_dir`` as the shim directory
  and an unrelated ``~/.local/bin/python`` as "Hermes' Python";
* the known-root branches returned ``candidate.resolve()``, and a venv's
  ``bin/python`` is a symlink to its base interpreter, so the venv was discarded.
"""

import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from mnemosyne_hermes import install


def _write_executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _make_venv(root: Path, *, python_target: Path | None = None) -> Path:
    """Create a directory that looks like a real venv. Returns its bin/python.

    ``pyvenv.cfg`` is the marker that separates a venv from a shim directory
    such as ``~/.local/bin``, which can hold both a ``hermes`` launcher and an
    unrelated ``python`` without being a venv at all.
    """
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (root / "pyvenv.cfg").write_text(
        "home = /usr/bin\ninclude-system-site-packages = false\n", encoding="utf-8"
    )
    python = bin_dir / "python"
    if python_target is not None:
        python.symlink_to(python_target)
    else:
        _write_executable(python, "#!/bin/sh\nexit 0\n")
    return python


@dataclass
class _World:
    home: Path
    shims: Path
    venv: Path
    venv_python: Path
    shim_python: Path


@pytest.fixture
def hermes_world(tmp_path, monkeypatch):
    """A wrapper launcher on PATH, an unrelated sibling python, a real Hermes venv.

    Every other discovery signal is neutralized so each test opts in to exactly
    the layout it is exercising: ``VIRTUAL_ENV`` is cleared, ``sys.prefix`` is
    forced to look like a non-venv interpreter, and ``Path.home`` points at a
    temporary directory so a real ``~/hermes-agent`` cannot leak in.
    """
    home = tmp_path / "hermes-home"
    fake_user_home = tmp_path / "user-home"
    fake_user_home.mkdir(parents=True)

    venv = home / "hermes-agent" / "venv"
    venv_python = _make_venv(venv)
    _write_executable(venv / "bin" / "hermes", "#!/bin/sh\nexit 0\n")

    shims = tmp_path / "shims"
    _write_executable(
        shims / "hermes",
        "#!/usr/bin/env bash\n"
        "unset PYTHONPATH\n"
        "unset PYTHONHOME\n"
        f'exec "{venv / "bin" / "hermes"}" "$@"\n',
    )
    shim_python = _write_executable(shims / "python", "#!/bin/sh\nexit 0\n")

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("PATH", str(shims))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_user_home))

    return _World(
        home=home,
        shims=shims,
        venv=venv,
        venv_python=venv_python,
        shim_python=shim_python,
    )


def test_validation_does_not_regress_wrapper_resolution(hermes_world):
    """The #620 path must still win once the candidate has to prove it is a venv."""
    found = install._find_hermes_python()

    assert found == hermes_world.venv_python
    assert found != hermes_world.shim_python


def test_non_exec_launcher_does_not_hijack_a_known_hermes_root(tmp_path, monkeypatch):
    """A launcher that neither symlinks nor `exec`s resolves to its own shim dir.

    `_resolve_hermes_bin` correctly reports this launcher as the binary, so the
    sibling `python` is a shim-directory neighbour rather than a venv member.
    The valid Hermes root must win instead of `~/.local/bin/python`.
    """
    shims = tmp_path / "local" / "bin"
    # No `exec`: the wrapper runs the real binary as a subprocess and waits.
    _write_executable(
        shims / "hermes",
        '#!/usr/bin/env bash\n"/opt/hermes/real/bin/hermes" "$@"\n',
    )
    shim_python = _write_executable(shims / "python", "#!/bin/sh\nexit 0\n")

    home = tmp_path / "hermes-home"
    venv_python = _make_venv(home / "hermes-agent" / "venv")

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("PATH", str(shims))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user-home"))

    found = install._find_hermes_python()

    assert found == venv_python
    assert found != shim_python


def test_explicit_python_is_authoritative(hermes_world, tmp_path):
    """--python wins over every probe, including a valid PATH venv."""
    chosen = _make_venv(tmp_path / "chosen")

    assert install._find_hermes_python(explicit_python=chosen) == chosen
    assert install._find_hermes_python(explicit_python=str(chosen)) == chosen


def test_venv_python_symlink_is_not_resolved(tmp_path, monkeypatch):
    """A venv's bin/python symlinks to its base interpreter; resolving loses the venv."""
    base = _write_executable(tmp_path / "base" / "bin" / "python3.11", "#!/bin/sh\nexit 0\n")
    home = tmp_path / "hermes-home"
    venv_python = _make_venv(home / "hermes-agent" / "venv", python_target=base)

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user-home"))

    found = install._find_hermes_python()

    assert found == venv_python
    assert found != base
    assert found.resolve() == base  # the symlink is real; we just must not follow it


def test_path_sibling_is_used_when_it_is_a_real_venv(tmp_path, monkeypatch):
    """Do not regress #388: a launcher inside a genuine venv still wins early."""
    venv = tmp_path / "usr" / "local" / "lib" / "custom-hermes" / "venv"
    venv_python = _make_venv(venv)
    _write_executable(venv / "bin" / "hermes", "#!/bin/sh\nexit 0\n")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("PATH", str(venv / "bin"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user-home"))

    assert install._find_hermes_python() == venv_python


def test_unvalidated_launcher_sibling_is_never_returned(tmp_path, monkeypatch):
    """No validated venv means no candidate at all, not a plausible-looking guess.

    The sibling here is the shape of a Homebrew or system interpreter sitting
    next to a launcher. Returning it is what let `mnemosyne-hermes[all]` be
    installed into an unrelated Python, so discovery gives up instead.
    """
    system_bin = tmp_path / "usr" / "bin"
    _write_executable(system_bin / "hermes", "#!/bin/sh\nexit 0\n")
    system_python = _write_executable(system_bin / "python", "#!/bin/sh\nexit 0\n")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("PATH", str(system_bin))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user-home"))

    found = install._find_hermes_python()

    assert found is None
    assert found != system_python


def test_known_root_without_pyvenv_cfg_is_rejected(tmp_path, monkeypatch):
    """A directory named `venv` is not evidence that it is one.

    Held to the same bar as the launcher sibling: a half-removed environment,
    or one whose base interpreter is gone, still has bin/python sitting there.
    """
    home = tmp_path / "hermes-home"
    bin_dir = home / "hermes-agent" / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    damaged = _write_executable(bin_dir / "python", "#!/bin/sh\nexit 0\n")
    assert not (bin_dir.parent / "pyvenv.cfg").exists()

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user-home"))

    found = install._find_hermes_python()

    assert found is None
    assert found != damaged

    # Same layout, now a real venv: the root is found again, so the rejection
    # above is the missing pyvenv.cfg and not the path shape.
    (bin_dir.parent / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    assert install._find_hermes_python() == damaged


def test_run_install_fails_clearly_when_nothing_validates(tmp_path, monkeypatch, capsys):
    """The no-validated-venv path must stop and name --python, not install anyway."""
    system_bin = tmp_path / "usr" / "bin"
    _write_executable(system_bin / "hermes", "#!/bin/sh\nexit 0\n")
    _write_executable(system_bin / "python", "#!/bin/sh\nexit 0\n")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("PATH", str(system_bin))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user-home"))
    monkeypatch.setattr(install, "check_mnemosyne_core", lambda: True)

    def _fail(*args, **kwargs):
        raise AssertionError("must not act without a validated Hermes runtime")

    monkeypatch.setattr(install, "_bootstrap_hermes_venv", _fail)
    monkeypatch.setattr(install, "install_plugin", _fail)
    monkeypatch.setattr(install, "install_bundled_skill", _fail)

    rc = install.run_install(hermes_home_path=tmp_path / "empty-home")

    assert rc == 1
    assert "--python" in capsys.readouterr().err


def test_no_bootstrap_continues_without_a_validated_interpreter(tmp_path, monkeypatch, capsys):
    """--no-bootstrap already forbids touching Hermes' venv, so nothing to prevent.

    Failing here would also preempt the guard that refuses to replace an
    existing wrapper install, replacing a data-safety message with a discovery
    one.
    """
    system_bin = tmp_path / "usr" / "bin"
    _write_executable(system_bin / "hermes", "#!/bin/sh\nexit 0\n")
    _write_executable(system_bin / "python", "#!/bin/sh\nexit 0\n")

    class _SkillResult:
        message = "skipped"

    link = tmp_path / "plugin-link"
    link.symlink_to(tmp_path)

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("PATH", str(system_bin))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user-home"))
    monkeypatch.setattr(install, "check_mnemosyne_core", lambda: True)
    monkeypatch.setattr(install, "install_plugin", lambda **kwargs: link)
    monkeypatch.setattr(install, "install_bundled_skill", lambda **kwargs: _SkillResult())

    def _fail(*args, **kwargs):
        raise AssertionError("--no-bootstrap must never bootstrap")

    monkeypatch.setattr(install, "_bootstrap_hermes_venv", _fail)

    rc = install.run_install(hermes_home_path=tmp_path / "empty-home", no_bootstrap=True)

    assert rc == 0
    assert "Continuing without dependency validation" in capsys.readouterr().err


def test_wrapper_mode_is_unaffected_by_failed_discovery(tmp_path, monkeypatch):
    """Wrapper installs validate their own interpreter and must not be blocked."""
    system_bin = tmp_path / "usr" / "bin"
    _write_executable(system_bin / "hermes", "#!/bin/sh\nexit 0\n")

    class _SkillResult:
        message = "skipped"

    target = tmp_path / "wrapper-target"
    target.mkdir()

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("PATH", str(system_bin))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user-home"))
    monkeypatch.setattr(install, "check_mnemosyne_core", lambda: True)
    monkeypatch.setattr(install, "install_plugin", lambda **kwargs: target)
    monkeypatch.setattr(install, "install_bundled_skill", lambda **kwargs: _SkillResult())
    monkeypatch.setattr(
        install,
        "plugin_state",
        lambda **kwargs: install.PluginState(
            status="installed",
            installed=True,
            target=target,
            mode="wrapper",
            message="ok",
        ),
    )

    rc = install.run_install(hermes_home_path=tmp_path / "empty-home", mode="wrapper")

    assert rc == 0


def test_run_install_bootstraps_hermes_venv_not_path_sibling(
    hermes_world, tmp_path, monkeypatch
):
    """The maintainer's acceptance criterion, at the bootstrap call site."""
    bootstrapped: list[Path] = []

    class _SkillResult:
        message = "skipped"

    link = tmp_path / "plugin-link"
    link.symlink_to(hermes_world.home)

    monkeypatch.setattr(install, "check_mnemosyne_core", lambda: True)
    monkeypatch.setattr(install, "check_mnemosyne_core_for_hermes_python", lambda python: None)
    monkeypatch.setattr(
        install,
        "_bootstrap_hermes_venv",
        lambda python: (bootstrapped.append(Path(python)), True)[1],
    )
    monkeypatch.setattr(install, "install_plugin", lambda **kwargs: link)
    monkeypatch.setattr(install, "install_bundled_skill", lambda **kwargs: _SkillResult())

    rc = install.run_install(hermes_home_path=hermes_world.home)

    assert rc == 0
    assert bootstrapped == [hermes_world.venv_python]
    assert hermes_world.shim_python not in bootstrapped


def test_dry_run_reports_explicitly_selected_interpreter(tmp_path, monkeypatch, capsys):
    """`install --dry-run --python X` must report X, not what discovery would pick.

    Covers the `main()` dry-run route rather than `run_install()`. The launcher
    here sits in a genuine venv, so discovery would return it if `--python` were
    not threaded through, which is what this pins down.
    """
    chosen = _make_venv(tmp_path / "chosen")

    launcher_venv = tmp_path / "path-venv"
    discovered = _make_venv(launcher_venv)
    _write_executable(launcher_venv / "bin" / "hermes", "#!/bin/sh\nexit 0\n")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", str(launcher_venv / "bin"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user-home"))

    rc = install.main(["install", "--dry-run", "--python", str(chosen)])

    out = capsys.readouterr().out
    assert rc == 0
    assert f"Hermes Python: {chosen}" in out
    assert str(discovered) not in out


def test_run_install_honours_explicit_python(hermes_world, tmp_path, monkeypatch):
    """--python reaches discovery in symlink mode, not just wrapper mode."""
    bootstrapped: list[Path] = []
    chosen = _make_venv(tmp_path / "chosen")

    class _SkillResult:
        message = "skipped"

    link = tmp_path / "plugin-link"
    link.symlink_to(hermes_world.home)

    monkeypatch.setattr(install, "check_mnemosyne_core", lambda: True)
    monkeypatch.setattr(install, "check_mnemosyne_core_for_hermes_python", lambda python: None)
    monkeypatch.setattr(
        install,
        "_bootstrap_hermes_venv",
        lambda python: (bootstrapped.append(Path(python)), True)[1],
    )
    monkeypatch.setattr(install, "install_plugin", lambda **kwargs: link)
    monkeypatch.setattr(install, "install_bundled_skill", lambda **kwargs: _SkillResult())

    rc = install.run_install(hermes_home_path=hermes_world.home, python=chosen)

    assert rc == 0
    assert bootstrapped == [chosen]
