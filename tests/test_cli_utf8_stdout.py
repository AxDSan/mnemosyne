"""Regression test: the CLI must not crash when stdout is a cp1252 pipe.

Agent tooling (and CI shells) frequently spawn the CLI with piped stdout. On
Windows, Python then defaults sys.stdout to the cp1252 codec, and printing a
memory whose content contains a character outside cp1252 (e.g. '\\u20b1')
raised UnicodeEncodeError and killed the whole command. run_cli() now
reconfigures stdout/stderr to UTF-8 with errors='replace' at startup.
"""

import io
import sys

from mnemosyne import cli


class _FakeMemory:
    def recall(self, query, top_k=5, explain=False):
        return [
            {
                "id": "abc123",
                "content": "salary \u20b135,000 per month \u2014 with \u2192 arrow",
                "score": 0.9,
            }
        ]


def test_run_cli_recall_survives_cp1252_pipe(monkeypatch):
    monkeypatch.setattr(cli, "_get_memory", lambda: _FakeMemory())

    buffer = io.BytesIO()
    monkeypatch.setattr(
        sys, "stdout", io.TextIOWrapper(buffer, encoding="cp1252", write_through=True)
    )
    monkeypatch.setattr(sys, "argv", ["mnemosyne", "recall", "peso salary"])

    cli.run_cli()  # must not raise UnicodeEncodeError

    output = buffer.getvalue().decode("utf-8")
    assert "salary" in output
    assert "abc123" in output
