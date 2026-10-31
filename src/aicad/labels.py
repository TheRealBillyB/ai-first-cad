"""Names and notes shown to a person or to the assistant.

A bidi control or a zero-width character can make a label look like a
different string from the one that is stored. A letter from another script
can look like a Latin letter. Labels used as titles and file names are
refused when that happens. Notes lose the hidden characters so the text on
screen is the text that was stored.
"""

from __future__ import annotations

import re
import unicodedata

# ESC sequences a terminal would act on. The letter that ends the sequence stays out.
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_CSI = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]")
_ESC = re.compile(r"\x1b[@-_]")
_KEEP_CONTROL = frozenset("\n\t")

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

    Bidi controls and zero-width characters are format characters. Control
    characters, surrogates, and terminal sequences are dropped too. A line
    feed or a tab is kept. After this, the characters that remain are the
    ones a reader sees.
    """

    if not isinstance(text, str):
        return ""
    cleaned = _OSC.sub("", text)
    cleaned = _CSI.sub("", cleaned)
    cleaned = _ESC.sub("", cleaned)
    normalized = unicodedata.normalize("NFKC", cleaned)
    kept: list[str] = []
    for char in normalized:
        if char in _KEEP_CONTROL:
            kept.append(char)
            continue
        if unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            continue
        kept.append(char)
    return "".join(kept)


def plain_line(text: str) -> str:
    """One line that can be printed. Line breaks are spaces."""

    return " ".join(strip_hidden(text).split())


def has_control_line(text: str) -> bool:
    """True when ``text`` contains a line break or another control character.

    A line separator and a paragraph separator are included. A letter from
    another script is not.
    """

    if not isinstance(text, str):
        return True
    for char in text:
        if unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            return True
    return False


def has_hidden_control(text: str) -> bool:
    """True when ``text`` has a line break other than a newline or a tab.

    A drawing may show a newline the author wrote. A line separator, a
    paragraph separator, a carriage return, or another line-breaking control
    is not that newline. A format character is not a line break. Hidden
    format characters are removed separately.
    """

    if not isinstance(text, str):
        return True
    for char in text:
        if char in _KEEP_CONTROL:
            continue
        if unicodedata.category(char) in {"Cc", "Zl", "Zp"}:
            return True
    return False


def require_one_line(text: str, label: str, error: type[Exception]) -> str:
    """``text`` unchanged when it is one line. A control character is refused."""

    if not isinstance(text, str) or has_control_line(text) or not text.strip():
        raise error(f"{label} has to stay on one line.")
    return text


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
