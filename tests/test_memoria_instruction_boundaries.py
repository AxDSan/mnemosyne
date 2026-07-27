"""Regression tests for MEMORIA instruction-pattern word boundaries (issue #507).

Without a leading `\\b`, the instruction pattern's negation/modal alternatives matched
*inside* longer words under `re.IGNORECASE`:

    "Good - whenever needed we can use it."  ->  stored "never needed we can use it"
    "Das Knie schmerzt seit Tagen"           ->  stored "nie schmerzt seit Tagen"

Because `memoria_instructions` rows are surfaced at recall time as user constraints, an
inverted instruction is worse than a missing one, so this is pinned per locale.

These tests exercise the compiled pattern exactly as `extract_and_store_facts` builds it
(same `IMPVERBS` substitution, same `re.IGNORECASE`) and deliberately avoid touching
SQLite so they stay fast and platform-independent.
"""

import re

import pytest

from mnemosyne.core.beam import BeamMemory


def compiled_instruction_pattern(lang: str) -> str:
    """Rebuild the instruction regex the way the extractor does at runtime."""
    pat = BeamMemory.MULTILINGUAL_PATTERNS[lang]
    return pat["instruction"].replace("IMPVERBS", pat["instruction_imperative"])


def matches(lang: str, text: str):
    return [m.group(0) for m in re.finditer(compiled_instruction_pattern(lang), text, re.IGNORECASE)]


class TestInstructionPatternWordBoundary:
    """A modal/negation keyword must not match inside a longer word."""

    @pytest.mark.parametrize(
        "text",
        [
            # Verbatim from the issue's production audit (rows 59 and 38).
            "Good - whenever needed we can use it. I've disabled it.",
            "Next useful step, whenever you're ready to continue",
            "whenever the agent creates a temporary folder for a smoke test",
        ],
    )
    def test_en_whenever_does_not_yield_never_instruction(self, text):
        assert matches("en", text) == [], (
            "'never' matched inside 'whenever', which inverts the user's meaning"
        )

    def test_de_nie_does_not_match_inside_knie(self):
        assert matches("de", "Das Knie schmerzt seit Tagen und wird nicht besser") == [], (
            "'nie' matched inside 'Knie'"
        )

    @pytest.mark.parametrize(
        ("lang", "text"),
        [
            ("en", "never commit directly to the main branch please"),
            ("en", "always run the full test suite before pushing changes"),
            ("en", "must not store secrets in the repository configuration"),
            ("de", "nie ohne Tests committen bitte beachten"),
            ("de", "immer die Tests vor dem Pushen ausfuehren bitte"),
            ("it", "mai committare direttamente sul branch principale"),
            ("es", "siempre ejecuta las pruebas antes de subir los cambios"),
        ],
    )
    def test_genuine_instructions_still_extract(self, lang, text):
        """The boundary must not cost us real instructions at a word start."""
        assert matches(lang, text), f"legitimate {lang} instruction no longer extracted"

    @pytest.mark.parametrize(
        ("lang", "text"),
        [
            # A keyword preceded by punctuation/quote is still at a word boundary.
            ("en", '"never push to main without a review" is the rule here'),
            ("en", "- always rebase before opening the pull request"),
        ],
    )
    def test_boundary_allows_punctuation_prefix(self, lang, text):
        assert matches(lang, text), "word boundary should still match after punctuation"

    def test_every_locale_instruction_pattern_is_boundary_anchored(self):
        """Guard the whole table so a new locale cannot reintroduce issue #507."""
        unanchored = [
            lang
            for lang, pat in BeamMemory.MULTILINGUAL_PATTERNS.items()
            if not pat["instruction"].startswith(r"\b")
        ]
        assert unanchored == [], f"instruction pattern not word-boundary anchored: {unanchored}"
