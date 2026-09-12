"""Spoken KES amounts for the price guard (epic #10, contract 3).

The voice model may only record a price the provider actually said. `amount_heard` checks a
claimed amount against the provider's transcribed words. Every ambiguity here resolves toward
"not heard": the agent then asks the provider to repeat, which is safe; guessing is not.
"""

import re

UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
}  # fmt: skip
TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}  # fmt: skip
TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip
SCALES = {"thousand": 1_000, "k": 1_000, "million": 1_000_000}

# Terminators that may follow a bare number read as an implied-thousands price ("I charge
# twenty three" -> KES 23,000). An allowlist, not a denylist: every ambiguity resolves toward
# "not heard" (module docstring), so a bare number is only trusted as a price when nothing
# follows it (end of utterance/clause, or punctuation) or an explicit currency word does.
# Anything else -- "years", "months", "times", "km", any noun not listed here -- is rejected.
MONEY_TERMINATORS = frozenset(
    {None, ".", ",", ";", ":", "!", "?", "shilling", "shillings", "bob", "ksh", "kes", "shs"}
)

# Which token kinds may continue a number phrase after the previous kind.
_ALLOWED_AFTER: dict[str, frozenset[str | None]] = {
    "digit": frozenset({None, "scale", "hundred"}),
    "unit": frozenset({None, "tens", "hundred", "scale", "and"}),
    "teen": frozenset({None, "hundred", "scale", "and"}),
    "tens": frozenset({None, "hundred", "scale", "and"}),
    "hundred": frozenset({None, "unit", "teen", "tens", "digit"}),
    "scale": frozenset({None, "unit", "teen", "tens", "hundred", "digit"}),
}

_DIGIT_GROUP_SEPARATOR = re.compile(r"(?<=\d)[, ](?=\d{3}(?!\d))")  # 21,000 / 21 000
_LETTER_DIGIT_BOUNDARY = re.compile(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])")  # ksh21000 / 23k
_TOKEN = re.compile(r"\d+(?:\.\d+)?|[a-z]+|[,.;:!?]")


def _classify(token: str) -> tuple[str | None, float]:
    if token[0].isdigit():
        return "digit", float(token)
    if token in UNITS:
        return "unit", UNITS[token]
    if token in TEENS:
        return "teen", TEENS[token]
    if token in TENS:
        return "tens", TENS[token]
    if token == "hundred":
        return "hundred", 100
    if token in SCALES:
        return "scale", SCALES[token]
    if token == "and":
        return "and", 0
    return None, 0


def _parse(text: str) -> list[tuple[int, str | None]]:
    """Whole-number amounts in `text`, each paired with the token that ended its phrase."""
    normalized = _DIGIT_GROUP_SEPARATOR.sub("", text.lower().replace("-", " "))
    tokens = _TOKEN.findall(_LETTER_DIGIT_BOUNDARY.sub(" ", normalized))

    found: list[tuple[int, str | None]] = []
    total = group = 0.0
    last: str | None = None
    smallest_scale: float | None = None

    def flush(terminator: str | None) -> None:
        nonlocal total, group, last, smallest_scale
        if last is not None:
            value = total + group
            if value == int(value):
                found.append((int(value), terminator))
        total = group = 0.0
        last = None
        smallest_scale = None

    for token in tokens:
        kind, value = _classify(token)
        if kind == "scale" and token == "k" and last != "digit":
            kind = None  # a bare "k" is just a letter
        if kind is None:
            flush(token)
            continue
        if kind == "and":
            if last in ("hundred", "scale"):
                last = "and"
            else:
                flush(token)
            continue

        if last not in _ALLOWED_AFTER[kind]:
            flush(token)
        elif kind == "scale" and smallest_scale is not None and value >= smallest_scale:
            # "twenty thousand twenty thousand": the second group starts a new amount.
            carry, group = group, 0.0
            flush(token)
            group, last = carry, "digit"

        if kind == "hundred":
            group = (group or 1) * 100
        elif kind == "scale":
            total += (group or 1) * value
            group = 0.0
            smallest_scale = value
        else:
            group += value
        last = kind

    flush(None)
    return found


def spoken_amounts(text: str) -> set[int]:
    return {value for value, _ in _parse(text)}


def amount_heard(amount: int, text: str) -> bool:
    """True if the provider's words contain `amount`.

    Also accepts conversational thousands ("I charge twenty-three" for KES 23,000), but only
    when nothing follows the bare number, or what follows is punctuation or a currency word
    (see MONEY_TERMINATORS). "Twenty three years"/"twenty three times" are never money.
    """
    parsed = _parse(text)
    if any(value == amount for value, _ in parsed):
        return True
    if amount % 1000 or not 0 < amount // 1000 < 1000:
        return False
    return any(
        value == amount // 1000 and terminator in MONEY_TERMINATORS for value, terminator in parsed
    )
