"""Total assembled prefetch budget (prefetch_char_limit).

Per-item MNEMOSYNE_PREFETCH_CONTENT_CHARS is a different knob. This suite
caps the joined working_injection block the way MEMORY.md is already capped.
"""
from __future__ import annotations

from hermes_memory_provider import (
    DEFAULT_PREFETCH_CHAR_LIMIT,
    MnemosyneMemoryProvider,
    PrefetchProfile,
    apply_prefetch_char_budget,
    prefetch_omit_footer,
    register_profile,
)


OMIT_NEEDLE = "more hits omitted; call mnemosyne_recall."


def _hit_line(i: int, pad: int = 120) -> str:
    return f"  [2026-05-14T12:00] (importance 0.90) Paris is the capital of France hit-{i:02d} {'x' * pad}"


def _twenty_hits(pad: int = 120) -> list[str]:
    return [_hit_line(i, pad=pad) for i in range(20)]


def _recall_rows(n: int, pad: int = 180) -> list[dict]:
    rows = []
    for i in range(n):
        rows.append(
            {
                "content": (
                    f"Paris is the capital of France unique-{i:02d} "
                    f"{'padding-' + str(i) + ' '}"
                    f"{'z' * pad}"
                ),
                "timestamp": "2026-05-14T12:00:00Z",
                "importance": 0.9,
                "score": 0.9,
                "keyword_score": 0.9,
                "trust_tier": "STATED",
            }
        )
    return rows


class FakeBeam:
    author_id = "test-author"

    def __init__(self, results):
        self.results = results

    def recall(self, **kwargs):
        return self.results


def _provider(results, *, limit=None, profile="t-budget-20"):
    register_profile(
        PrefetchProfile(
            name="t-budget-20",
            sources=("bank",),
            top_k=20,
            semantic_dedup=False,
        )
    )
    p = MnemosyneMemoryProvider()
    p._beam = FakeBeam(results)
    p._prefetch_profile = profile
    p._agent_context = "test"
    p._skip_contexts = set()
    if limit is not None:
        p._prefetch_char_limit = limit
    return p


def test_default_prefetch_char_limit_is_3000():
    assert DEFAULT_PREFETCH_CHAR_LIMIT == 3000
    provider = MnemosyneMemoryProvider()
    assert provider._prefetch_char_limit == 3000
    schema = {entry["key"]: entry for entry in provider.get_config_schema()}
    assert schema["prefetch_char_limit"]["default"] == 3000


def test_under_budget_has_no_omit_footer():
    hits = ["  short identity", "  short bank hit"]
    block = apply_prefetch_char_budget(hits, limit=3000)
    assert OMIT_NEEDLE not in block
    assert "short identity" in block
    assert "short bank hit" in block


def test_twenty_hits_cannot_exceed_limit_plus_footer():
    limit = DEFAULT_PREFETCH_CHAR_LIMIT
    block = apply_prefetch_char_budget(_twenty_hits(pad=200), limit=limit)
    footer = prefetch_omit_footer(1)
    assert len(block) <= limit + len("\n") + len(footer) + 8  # N can be two digits
    assert OMIT_NEEDLE in block
    assert block.splitlines()[-1].startswith("…")
    assert "call mnemosyne_recall." in block.splitlines()[-1]


def test_truncated_footer_counts_omitted_hits():
    hits = [f"hit-{i} " + ("y" * 40) for i in range(10)]
    # First two hits plus joiner sit under a tight cap; the rest are omitted.
    limit = len(hits[0]) + 1 + len(hits[1])
    block = apply_prefetch_char_budget(hits, limit=limit)
    assert block.startswith(hits[0])
    assert hits[1] in block
    assert prefetch_omit_footer(8) in block
    assert hits[2] not in block


def test_keeps_prefix_when_later_hits_overflow():
    identity = "  [IDENTITY] Ada is the user we are talking to"
    later = [f"  later-hit-{i} " + ("w" * 80) for i in range(12)]
    block = apply_prefetch_char_budget([identity, *later], limit=200)
    assert "[IDENTITY] Ada is the user" in block
    assert OMIT_NEEDLE in block
    assert "later-hit-11" not in block


def test_prefetch_twenty_recall_hits_respect_budget():
    p = _provider(_recall_rows(20, pad=200))
    block = p.prefetch("Paris capital France")
    footer = prefetch_omit_footer(1)
    assert block
    assert len(block) <= p._prefetch_char_limit + len("\n") + len(footer) + 8
    assert OMIT_NEEDLE in block


def test_prefetch_keeps_identity_omits_later_bank_hits():
    p = _provider(_recall_rows(20, pad=160), limit=350)
    p._identity_fichas = lambda: [
        {
            "content": "Ada Lovelace is the user on this session.",
            "importance": 0.95,
            "timestamp": "2026-05-14T12:00:00Z",
        }
    ]
    block = p.prefetch("Paris capital France")
    assert "[IDENTITY]" in block
    assert "Ada Lovelace is the user on this session." in block
    assert OMIT_NEEDLE in block


def test_skip_context_still_returns_empty():
    p = _provider(_recall_rows(20))
    p._agent_context = "cron"
    p._skip_contexts = {"cron"}
    assert p.prefetch("Paris capital France") == ""


def test_invalid_prefetch_char_limit_warns_and_defaults(tmp_path, caplog):
    (tmp_path / "config.yaml").write_text(
        "memory:\n  provider: mnemosyne\n  mnemosyne:\n    prefetch_char_limit: banana\n"
    )
    p = MnemosyneMemoryProvider()
    p._hermes_home = str(tmp_path)
    with caplog.at_level("WARNING"):
        p._apply_provider_config({})
    assert p._prefetch_char_limit == 3000
    assert "prefetch_char_limit" in caplog.text


def test_kwargs_prefetch_char_limit_is_applied():
    p = MnemosyneMemoryProvider()
    p._apply_provider_config({"prefetch_char_limit": 1200})
    assert p._prefetch_char_limit == 1200


def test_config_yaml_prefetch_char_limit_is_applied(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "memory:\n  provider: mnemosyne\n  mnemosyne:\n    prefetch_char_limit: 900\n"
    )
    p = MnemosyneMemoryProvider()
    p._hermes_home = str(tmp_path)
    p._apply_provider_config({})
    assert p._prefetch_char_limit == 900
