"""Names and notes shown to a person or to the assistant.

A bidi control or a zero-width character can make a label look like a
different string from the one that is stored. A letter from another script
can look like a Latin letter. Labels used as titles and file names are
refused when that happens. Notes lose the hidden characters so the text on
screen is the text that was stored.
"""

from __future__ import annotations

import unicodedata

# Letters that look like Latin letters. A word in one other script is kept.
_CONFUSABLES = {
    "а": "a",
    "е": "e",
    "о": "o",
    "р": "p",
    "с": "c",
    "у": "y",
    "х": "x",
    "і": "i",
    "ѕ": "s",
    "һ": "h",
    "ј": "j",
    "ԁ": "d",
    "ԛ": "q",
    "ԝ": "w",
    "А": "A",
    "В": "B",
    "Е": "E",
    "К": "K",
    "М": "M",
    "Н": "H",
    "О": "O",
    "Р": "P",
    "С": "C",
    "Т": "T",
    "Х": "X",
    "І": "I",
    "Α": "A",
    "Β": "B",
    "Ε": "E",
    "Ζ": "Z",
    "Η": "H",
    "Ι": "I",
    "Κ": "K",
    "Μ": "M",
    "Ν": "N",
    "Ο": "O",
    "Ρ": "P",
    "Τ": "T",
    "Υ": "Y",
    "Χ": "X",
    "α": "a",
    "ο": "o",
    "ρ": "p",
    "υ": "u",
    "χ": "x",
}
_CONFUSABLE_MAP = str.maketrans(_CONFUSABLES)


def strip_hidden(text: str) -> str:
    """Drop format characters and apply compatibility normalization.

    Bidi controls and zero-width characters are format characters. After this,
    the characters that remain are the ones a reader sees.
    """

    if not isinstance(text, str):
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(char for char in normalized if unicodedata.category(char) != "Cf")


def spoofs_latin(text: str) -> bool:
    """True when ``text`` would be read as Latin but is not that text.

    A title written entirely in another script is not a spoof. A Latin word
    with one lookalike letter is. So is a lookalike word whose letters are
    not Latin at all.
    """

    if not isinstance(text, str) or not any(char in _CONFUSABLES for char in text):
        return False
    skeleton = text.translate(_CONFUSABLE_MAP)
    if skeleton.isascii() and not text.isascii():
        return True
    return any(char.isascii() and char.isalpha() for char in text)


def plain_title(text: str, label: str, error: type[Exception]) -> str:
    """A title or file name that shows the same text that is stored."""

    cleaned = strip_hidden(text).strip()
    if spoofs_latin(cleaned):
        raise error(f"{label} has a character that looks like a different letter.")
    return cleaned


def misleading_path(text: str) -> bool:
    """True when a path contains hidden characters or a Latin lookalike."""

    if not isinstance(text, str):
        return False
    if strip_hidden(text) != text:
        return True
    for part in text.replace("\\", "/").split("/"):
        if part and spoofs_latin(part):
            return True
    return False
