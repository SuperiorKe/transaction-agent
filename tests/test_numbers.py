import pytest

from app.numbers import amount_heard, spoken_amounts


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("21000", {21_000}),
        ("I charge 21,000", {21_000}),
        ("it's 21 000 shillings", {21_000}),
        ("KES 21,000 for the day", {21_000}),
        ("Ksh21,000", {21_000}),
        ("21000/= only", {21_000}),
        ("20,000.00", {20_000}),
        ("1,000,000", {1_000_000}),
        ("it's 23k", {23_000}),
        ("21.5k", {21_500}),
        ("21 thousand", {21_000}),
        ("21 thousand 500", {21_500}),
        ("twenty-three thousand", {23_000}),
        ("twenty one thousand five hundred", {21_500}),
        ("nineteen thousand five hundred", {19_500}),
        ("one hundred thousand", {100_000}),
        ("two hundred and fifty thousand", {250_000}),
        ("a thousand", {1_000}),
        ("one million", {1_000_000}),
        ("I charge twenty three thousand for six hours", {23_000, 6}),
        ("I can do 19,500 or 20,000", {19_500, 20_000}),
        ("twenty, twenty-one", {20, 21}),
        ("twenty thousand twenty thousand", {20_000}),
        ("about 6.5 hours", set()),
        ("okay, no problem", set()),
        ("ok k", set()),
        ("five and six", {5, 6}),
        ("21 5", {5, 21}),
    ],
)
def test_spoken_amounts(text, expected):
    assert spoken_amounts(text) == expected


@pytest.mark.parametrize(
    ("amount", "text", "heard"),
    [
        (23_000, "I charge twenty three thousand for six hours", True),
        (23_000, "it's 23k", True),
        (23_000, "twenty-three", True),
        (20_000, "I can do twenty", True),
        (21_000, "I can do twenty", False),
        (23_500, "twenty-three", False),
        (6_000, "six hours of coverage", False),
        (50_000, "fifty photos edited", False),
        (1_000_000, "one thousand", False),
        (19_500, "Yes. 23,000, six hours.", False),
        (21_000, "Twenty... [inaudible]", False),
        # Regression: a bare number followed by an unrelated noun is never a price, even one
        # not on any hardcoded exclusion list (adversarial review, ship of arch/africastalking).
        (23_000, "I've been doing this for twenty three years", False),
        (15_000, "I need fifteen months to deliver", False),
        (5_000, "I called you five times", False),
        (23_000, "twenty three, that's my price", True),
        (20_000, "it's twenty shillings, I mean thousand", True),
    ],
)
def test_amount_heard(amount, text, heard):
    assert amount_heard(amount, text) is heard
