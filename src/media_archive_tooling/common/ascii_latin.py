"""ASCII Latin rendering and transliteration utilities."""
import unicodedata
import re

SPECIAL_CHAR_MAP = {
    "ß": "ss", "ẞ": "Ss",
    "æ": "ae", "Æ": "Ae",
    "œ": "oe", "Œ": "Oe",
    "ø": "o",  "Ø": "O",
    "å": "a",  "Å": "A",
    "ł": "l",  "Ł": "L",
    "đ": "d",  "Đ": "D",
    "ð": "d",  "Ð": "D",
    "þ": "th", "Þ": "Th",
}


def to_ascii_latin(text: str) -> str:
    """Transliterate Unicode string to clean ASCII Latin text.
    
    Examples:
        Zürich -> Zurich
        Průhonice -> Pruhonice
        Málaga -> Malaga
        Ljubljana -> Ljubljana
    """
    if not text:
        return ""
        
    for char, replacement in SPECIAL_CHAR_MAP.items():
        text = text.replace(char, replacement)
        
    # Decompose characters into base characters and diacritical marks
    normalized = unicodedata.normalize("NFKD", text)
    # Filter out combining diacritical marks
    ascii_chars = [c for c in normalized if not unicodedata.combining(c)]
    result = "".join(ascii_chars)
    
    # Ensure only ASCII remains
    result = result.encode("ascii", "ignore").decode("ascii")
    return result


def sanitize_filename_token(text: str, allow_hyphen: bool = True) -> str:
    """Clean a token for canonical filename usage (ASCII, alphanumeric and hyphens)."""
    text = to_ascii_latin(text)
    if allow_hyphen:
        text = re.sub(r"[^A-Za-z0-9\-]+", "-", text)
        text = re.sub(r"-+", "-", text).strip("-")
    else:
        text = re.sub(r"[^A-Za-z0-9]+", "", text)
    return text
