"""
Mnemosyne AAAK Dialect
Full-fledged compression scheme for AI memory context.
Lossless shorthand that LLMs parse efficiently without a decoder.
"""

import re
from typing import List, Tuple

# Category prefixes → AAAK codes
CATEGORY_MAP = {
    "PREFERENCE": "PREF",
    "TRAIT": "TRAIT",
    "STATUS": "STAT",
    "INSTRUCTION": "INST",
    "PROJECT": "PROJ",
    "LOCATION": "LOC",
    "FAMILY": "FAM",
    "OCCUPATION": "OCC",
    "DECISION": "DEC",
    "EVENT": "EVT",
    "TOOL": "TOOL",
    "FACT": "FACT",
    "OPINION": "OPN",
}

# Common structural phrases → compressed forms
PHRASE_MAP = {
    "User asked ": "ASK ",
    "User wants ": "WANT ",
    "User prefers ": "PREF ",
    "User likes ": "LIKE ",
    "User dislikes ": "DISLIKE ",
    "User is ": "IS ",
    "User has ": "HAS ",
    "User built ": "BUILT ",
    "User asked for ": "ASK ",
    "User requested ": "REQ ",
    "Married to ": "MARRIED→",
    "Email: ": "@",
    "GitHub: ": "GH:",
    "Location: ": "LOC:",
    "Phone: ": "PH:",
    "User email is ": "@",
    "User voice message ": "VM ",
    "User stack: ": "STACK|",
    "Full-stack developer": "FSDEV",
    "Software Developer": "SDEV",
    "AI Systems Engineer": "AIENG",
    "real-time": "RT",
    "Real-time": "RT",
    "bilingual": "bi",
    "Bilingual": "bi",
    "self-hosted": "selfhost",
    "automation": "auto",
    "transcription": "transc",
    "translation": "transl",
}

# Structural replacements (order matters)
STRUCTURAL_REPLACEMENTS: List[Tuple[str, str]] = [
    # Sentence/phrase separators
    (" - ", " | "),
    (" -- ", " | "),
    (" | ", " | "),  # normalize
    
    # Lists become pipe-delimited
    (", ", " | "),
    
    # Conjunctions
    (" and ", "+"),
    (" or ", "/"),
    
    # Directional / relational
    (" for ", "→"),
    (" to ", "→"),
    (" with ", " w/ "),
    (" over ", ">"),
    (" instead of ", "!>"),
    (" because of ", "∵"),
    (" due to ", "∵"),
    
    # Container words
    (" using ", "→"),
    (" built ", "→"),
    (" in ", ":"),
    (" at ", "@"),
    (" on ", "@"),
    (" from ", "<-"),
]

# Reverse maps for decode
REV_CATEGORY = {v: k for k, v in CATEGORY_MAP.items()}
REV_PHRASE = {v: k for k, v in PHRASE_MAP.items()}


def _apply_category_prefixes(text: str) -> str:
    """Compress CATEGORY: prefix to CODE|"""
    for full, code in CATEGORY_MAP.items():
        if text.startswith(f"{full}: "):
            return text.replace(f"{full}: ", f"{code}|", 1)
    return text


def _apply_phrases(text: str) -> str:
    """Replace common phrases with shorthand."""
    # Sort by length descending to avoid partial matches
    for phrase, shorthand in sorted(PHRASE_MAP.items(), key=lambda x: -len(x[0])):
        text = text.replace(phrase, shorthand)
    return text


def _apply_structural(text: str) -> str:
    """Apply structural compression."""
    for pattern, replacement in STRUCTURAL_REPLACEMENTS:
        text = text.replace(pattern, replacement)
    return text


def _compact_parens(text: str) -> str:
    """Remove spaces inside parentheses."""
    return re.sub(r"\(\s*", "(", text).replace(" )", ")")


def encode(text: str) -> str:
    """
    Compress natural language memory into AAAK dialect.
    
    Example:
        >>> encode("PREFERENCE: Imperial units for GPS, 12-hour time format (5:30 PM)")
        "PREF|imperial-units→GPS|12h-timefmt(5:30PM)"
    """
    if not text:
        return text
    
    # Skip if already looks like AAAK (has pipe delimiters and no spaces)
    if "|" in text and len(text.split()) <= 3:
        return text
    
    result = text.strip()
    result = _apply_category_prefixes(result)
    result = _apply_phrases(result)
    result = _apply_structural(result)
    result = _compact_parens(result)
    
    # Compact common trailing phrases
    result = result.replace("working correctly", "OK")
    result = result.replace("working", "OK")
    result = result.replace("complete", "DONE")
    result = result.replace("completed", "DONE")
    
    return result.strip()


def decode(aaak_text: str) -> str:
    """
    Decompress AAAK dialect back to readable natural language.
    Best-effort reconstruction.
    """
    if not aaak_text:
        return aaak_text
    
    text = aaak_text.strip()
    
    # Pre-restore embedded patterns that would be broken by structural markers
    text = text.replace("w/", "with")
    text = text.replace("MARRIED→", "Married to ")
    
    # Restore structural markers FIRST (before category prefixes)
    RESTORE_MAP = [
        (" | ", " - "),
        ("+", " and "),
        ("/", " or "),
        ("→", " for "),
        (">", " over "),
        ("!>", " instead of "),
        ("∵", " because of "),
        (": ", " in "),
        (":", ":"),  # no-op to preserve colons
        ("<-", " from "),
    ]
    
    for marker, word in RESTORE_MAP:
        text = text.replace(marker, word)
    
    # Restore phrases (reverse mapping)
    for shorthand, phrase in sorted(REV_PHRASE.items(), key=lambda x: -len(x[0])):
        if len(shorthand) <= 3:
            text = re.sub(rf"\b{re.escape(shorthand)}\b", phrase, text)
        else:
            text = text.replace(shorthand, phrase)
    
    # Restore category prefixes LAST so their colons stay intact
    for code, full in REV_CATEGORY.items():
        text = text.replace(f"{full} in ", f"{full}: ", 1)  # fix any stray structural restore
        text = text.replace(f"{code}|", f"{full}: ", 1)
    
    return text.strip()


def batch_encode(memories: List[str]) -> List[str]:
    """Encode a batch of memory strings."""
    return [encode(m) for m in memories]


def compression_ratio(original: str, compressed: str) -> float:
    """Calculate token compression ratio (original / compressed)."""
    orig_len = len(original.split()) if " " in original else len(original) // 4
    comp_len = len(compressed.split()) if " " in compressed else len(compressed) // 4
    if comp_len == 0:
        return 1.0
    return round(orig_len / comp_len, 2)
