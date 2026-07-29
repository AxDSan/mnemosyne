"""Word-boundary regression tests for the MEMORIA instruction extractor (#507).

The instruction pattern was unanchored, so ``never`` matched inside ``whenever``
and the extractor stored the opposite of what the user said. On a production
bank, "Good - whenever needed we can use it." was recorded as an instruction
"never needed we can use it".

Test design note: the pattern-level cases below rebuild the runtime regex the
same way ``extract_and_store_facts`` does. That mirrors beam.py's assembly, so
it is kept honest by ``test_extractor_end_to_end_does_not_invert_whenever``,
which drives the real extraction path and asserts on the stored row.

Credit: diagnosis and the original fix approach are from #508 (@Sanjays2402,
account since deleted) and #549 (@Souptik96).
"""

import re
import tempfile
from pathlib import Path

import pytest

from mnemosyne.core.beam import BeamMemory

# Locales whose instruction pattern must be boundary-anchored.
LOCALES = ["en", "de", "ru", "it", "es"]


def _runtime_instruction_re(locale: str) -> str:
    """Assemble the instruction regex exactly as beam.py:4845 does."""
    pat = BeamMemory.MULTILINGUAL_PATTERNS[locale]
    return pat["instruction"].replace("IMPVERBS", pat["instruction_imperative"])


@pytest.mark.parametrize("locale", LOCALES)
def test_every_locale_instruction_pattern_is_boundary_anchored(locale):
    """Guard: no locale may reintroduce an unanchored instruction pattern."""
    pattern = BeamMemory.MULTILINGUAL_PATTERNS[locale]["instruction"]
    assert pattern.startswith(r"\b"), (
        f"{locale} instruction pattern is not word-boundary anchored; "
        f"'never' will match inside 'whenever'"
    )


@pytest.mark.parametrize(
    "content",
    [
        "Good - whenever needed we can use it. I've disabled it.",
        "Use the cache whenever possible for the hot path lookups.",
        "Whenever the config changes we reload the whole provider stack.",
    ],
)
def test_en_whenever_does_not_yield_never_instruction(content):
    """'whenever' must not produce a 'never ...' instruction (the #507 report)."""
    matches = re.findall(_runtime_instruction_re("en"), content, re.IGNORECASE)
    assert matches == [], f"inverted instruction extracted from {content!r}: {matches}"


def test_de_nie_does_not_match_inside_knie():
    """German 'nie' must not match inside unrelated words such as 'Knie'."""
    matches = re.findall(
        _runtime_instruction_re("de"),
        "Das Knie tut weh und wir sollten das genauer untersuchen lassen.",
        re.IGNORECASE,
    )
    assert matches == []


@pytest.mark.parametrize(
    "content,expected_fragment",
    [
        ("never commit directly to the main branch please", "commit directly"),
        ("Please always run the tests before pushing to main.", "run the tests"),
        ("You must not store secrets in the config file ever.", "store secrets"),
        # The boundary must still allow a preceding word or punctuation.
        ("Note: never push to main without a review from someone.", "push to main"),
        ("wherever you go, always run the tests before you commit", "run the tests"),
    ],
)
def test_genuine_instructions_still_extract(content, expected_fragment):
    """No-regression: real instructions must survive the boundary anchor."""
    matches = re.findall(_runtime_instruction_re("en"), content, re.IGNORECASE)
    assert matches, f"genuine instruction lost: {content!r}"
    assert any(expected_fragment in m for m in matches), (
        f"expected {expected_fragment!r} in {matches}"
    )


def test_extractor_end_to_end_does_not_invert_whenever():
    """Drive the real extraction path and assert on the stored row.

    The pattern-level tests above bypass the false-positive filter, the bare
    'should' skip, and the memoria_instructions INSERT. This one does not, so a
    refactor of the regex assembly cannot leave those tests green while the
    shipped extractor regresses.
    """
    with tempfile.TemporaryDirectory() as tmp:
        mem = BeamMemory(session_id="test-507", db_path=Path(tmp) / "memories.db")
        try:
            mem.extract_and_store_facts(
                "Good - whenever needed we can use it. I've disabled it.",
                source_memory_id="mem-507",
            )
            stored = [
                row[0]
                for row in mem.conn.execute(
                    "SELECT instruction FROM memoria_instructions "
                    "WHERE source_memory_id = ?",
                    ("mem-507",),
                ).fetchall()
            ]
            assert not any("never" in s.lower() for s in stored), (
                f"extractor stored an inverted instruction: {stored}"
            )
        finally:
            mem.conn.close()


def test_extractor_end_to_end_still_stores_a_genuine_instruction():
    """Companion to the above: proves the end-to-end path can store at all.

    Without this, the inversion test would pass trivially if the extractor
    silently stored nothing (e.g. a renamed table or a changed signature).
    """
    with tempfile.TemporaryDirectory() as tmp:
        mem = BeamMemory(session_id="test-507b", db_path=Path(tmp) / "memories.db")
        try:
            mem.extract_and_store_facts(
                "Please never commit directly to the main branch, use a PR.",
                source_memory_id="mem-507b",
            )
            stored = [
                row[0]
                for row in mem.conn.execute(
                    "SELECT instruction FROM memoria_instructions "
                    "WHERE source_memory_id = ?",
                    ("mem-507b",),
                ).fetchall()
            ]
            assert stored, "extractor stored no instruction for a genuine directive"
            assert any("commit directly" in s.lower() for s in stored), stored
        finally:
            mem.conn.close()
