"""Regression coverage for Windows symlink-privilege install failures (#807)."""

from pathlib import Path

from mnemosyne_hermes import install


class _WindowsSymlinkPrivilegeError(OSError):
    """Cross-platform stand-in for Windows ERROR_PRIVILEGE_NOT_HELD (1314)."""

    def __init__(self) -> None:
        super().__init__("A required privilege is not held by the client")
        self.winerror = 1314


def test_run_install_explains_windows_symlink_privilege_and_safe_wrapper_retry(
    tmp_path, monkeypatch, capsys
):
    """WinError 1314 must fail closed with a retry using the resolved Hermes Python."""
    hermes_python = Path(r"C:\Program Files\Hermes\venv\Scripts\python.exe")
    target = tmp_path / "plugins" / "mnemosyne"

    monkeypatch.setattr(install, "check_mnemosyne_core", lambda: True)
    monkeypatch.setattr(install, "_find_hermes_python", lambda **kwargs: hermes_python)
    monkeypatch.setattr(install, "check_mnemosyne_core_for_hermes_python", lambda _: "4.0")
    monkeypatch.setattr(
        install.os,
        "symlink",
        lambda *_: (_ for _ in ()).throw(_WindowsSymlinkPrivilegeError()),
    )

    rc = install.run_install(hermes_home_path=tmp_path)
    stderr = capsys.readouterr().err

    assert rc == 1
    assert "Windows symbolic-link privilege" in stderr
    assert "Developer Mode" in stderr
    assert "persistent no-symlink-privilege alternative" in stderr
    assert (
        'mnemosyne-hermes install --mode wrapper --python '
        '"C:\\Program Files\\Hermes\\venv\\Scripts\\python.exe"'
    ) in stderr
    assert not target.exists()
    assert not target.is_symlink()
