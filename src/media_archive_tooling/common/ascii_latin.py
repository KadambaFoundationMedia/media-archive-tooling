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

# Deterministic Cyrillic transliteration
CYRILLIC_MAP = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "Yo", "Ж": "Zh", "З": "Z",
    "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M", "Н": "N", "О": "O", "П": "P", "Р": "R",
    "С": "S", "Т": "T", "У": "U", "Ф": "F", "Х": "Kh", "Ц": "Ts", "Ч": "Ch", "Ш": "Sh", "Щ": "Shch",
    "Ъ": "", "Ы": "Y", "Ь": "", "Э": "E", "Ю": "Yu", "Я": "Ya",
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    # Ukrainian / Serbian / Macedonian additions
    "Є": "Ye", "є": "ye", "І": "I", "і": "i", "Ї": "Yi", "ї": "yi", "Ґ": "G", "ґ": "g",
    "Ђ": "Dj", "ђ": "dj", "Љ": "Lj", "љ": "lj", "Њ": "Nj", "њ": "nj", "Ћ": "C", "ћ": "c",
    "Џ": "Dzh", "џ": "dzh", "Ў": "U", "ў": "u",
}

# Deterministic Devanagari / Hindi transliteration
DEVANAGARI_MAP = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri",
    "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "अं": "am", "अः": "ah",
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "ny",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    "ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ं": "m", "ः": "h", "्": "",
    "ॅ": "e", "ॉ": "o", "़": "",
    "०": "0", "१": "1", "२": "2", "३": "3", "४": "4",
    "५": "5", "६": "6", "७": "7", "८": "8", "९": "9",
}


def to_ascii_latin(text: str) -> str:
    """Transliterate Unicode string to clean ASCII Latin text.
    
    Handles Latin diacritics, Cyrillic, and Devanagari/Hindi.
    """
    if not text:
        return ""
        
    for char, replacement in SPECIAL_CHAR_MAP.items():
        text = text.replace(char, replacement)

    # Transliterate Cyrillic characters
    chars = []
    for c in text:
        if c in CYRILLIC_MAP:
            chars.append(CYRILLIC_MAP[c])
        elif c in DEVANAGARI_MAP:
            chars.append(DEVANAGARI_MAP[c])
        else:
            chars.append(c)
    text = "".join(chars)
        
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
