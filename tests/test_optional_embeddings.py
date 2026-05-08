"""Regression tests for base installs without optional embedding dependencies."""

import os
import subprocess
import sys
import textwrap


_BLOCK_OPTIONAL_DEPS = r"""
import importlib.abc
import sys

class BlockOptionalEmbeddingDeps(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "numpy" or fullname.startswith("numpy."):
            raise ModuleNotFoundError("No module named 'numpy'")
        if fullname == "fastembed" or fullname.startswith("fastembed."):
            raise ModuleNotFoundError("No module named 'fastembed'")
        return None

sys.meta_path.insert(0, BlockOptionalEmbeddingDeps())
"""


def _run_with_optional_embedding_deps_blocked(code: str, tmp_path):
    env = os.environ.copy()
    env["MNEMOSYNE_DATA_DIR"] = str(tmp_path / "mnemosyne-data")
    env["HOME"] = str(tmp_path / "home")
    return subprocess.run(
        [sys.executable, "-c", _BLOCK_OPTIONAL_DEPS + "\n" + code],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_embeddings_module_imports_without_numpy_or_fastembed(tmp_path):
    result = _run_with_optional_embedding_deps_blocked(
        textwrap.dedent(
            """
            from mnemosyne.core import embeddings

            assert embeddings.available() is False
            assert embeddings.embed_query("hello") is None
            assert embeddings.embed(["hello"]) is None
            """
        ),
        tmp_path,
    )

    assert result.returncode == 0, result.stderr


def test_cli_stats_works_without_optional_embedding_dependencies(tmp_path):
    result = _run_with_optional_embedding_deps_blocked(
        textwrap.dedent(
            """
            import sys
            from mnemosyne.cli import run_cli

            sys.argv = ["mnemosyne", "stats"]
            run_cli()
            """
        ),
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "Mnemosyne Stats" in result.stdout
    assert "Traceback" not in result.stderr
