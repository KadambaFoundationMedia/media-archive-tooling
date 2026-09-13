"""Deterministic whole-word title compaction for filename rendering (Build Plan Section 21)."""
import re
from typing import Tuple
from ..common.ascii_latin import to_ascii_latin


def sanitize_title_token(token: str) -> str:
    """Sanitize title text for inclusion in canonical filename."""
    ascii_val = to_ascii_latin(token)
    # Replace non-alphanumeric with hyphen, collapse repeated hyphens
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_val).strip("-")
    return clean


def compact_title_for_filename(
    full_title: str,
    max_title_chars: int,
    delimiter: str = "-"
) -> Tuple[str, bool, str]:
    """Shorten a long title deterministically at whole-word boundaries to fit within max_title_chars.

    Returns:
        (compacted_title, was_compacted, compaction_reason)
    """
    if not full_title:
        return "", False, ""

    # Split into words on spaces, underscores, or existing hyphens
    words = [w for w in re.split(r"[\s_\-]+", full_title.strip()) if w]
    if not words:
        return "", False, ""

    # Check if full title sanitized fits
    full_sanitized = delimiter.join(sanitize_title_token(w) for w in words if sanitize_title_token(w))
    if len(full_sanitized) <= max_title_chars:
        return full_sanitized, False, ""

    # Greedily accumulate whole words up to max_title_chars
    accumulated_words = []
    current_length = 0

    for word in words:
        clean_word = sanitize_title_token(word)
        if not clean_word:
            continue
        # Length if we add this word
        additional_len = len(clean_word) if not accumulated_words else (len(delimiter) + len(clean_word))
        if current_length + additional_len <= max_title_chars:
            accumulated_words.append(clean_word)
            current_length += additional_len
        else:
            # Cannot add this word without exceeding budget
            break

    # If even the first word exceeds the budget, keep the first word to avoid cutting it in half
    if not accumulated_words and words:
        first_clean = sanitize_title_token(words[0])
        compacted = first_clean
    else:
        compacted = delimiter.join(accumulated_words)

    reason = f"Title shortened at whole-word boundary to fit budget ({len(compacted)}/{max_title_chars} chars)"
    return compacted, True, reason
