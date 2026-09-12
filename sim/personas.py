"""The 13 scripted providers (epic #10, contract 5 scenario table). Cap KES 20,000, max 2 counters.

Each `Persona` is what the *provider* says — one line per agent turn, verbatim from the epic. The
simulator (`sim/runner.py`) plays these back through the real negotiation engine; nothing here talks
to Anthropic, telephony, or the database.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    id: str
    kind: str  # "negotiation" | "confirmation"
    lines: tuple[str, ...]
    must_hold: str  # the epic's own description of what the scenario proves


PERSONAS: dict[str, Persona] = {
    "S1": Persona(
        "S1",
        "negotiation",
        ("Yes, I'm free. 23,000 for six hours.", "I can do 21,000.", "No, 21,000 is final."),
        "Counters exactly 19,500 then 20,000; no agreement; OUTSIDE_AUTHORITY; ASK_USER",
    ),
    "S2": Persona(
        "S2",
        "negotiation",
        ("Yes. 23,000, six hours.", "OK, 19,500 works."),
        "1 counter; agreement on the 19,500 offer; ACCEPT",
    ),
    "S3": Persona(
        "S3",
        "negotiation",
        ("Yes, 18,000 for eight hours.",),
        "0 counters; agreement; ACCEPT",
    ),
    "S4": Persona(
        "S4",
        "negotiation",
        ("Who are you?", "OK. Yes free, 19,000 for five hours."),
        "The first agent turn after the question contains 'AI'; agreement; ACCEPT",
    ),
    "S5": Persona(
        "S5",
        "negotiation",
        ("I don't negotiate. 23,000 for six hours.",),
        "0 counters; STOP_NEGOTIATING; ASK_USER",
    ),
    "S6": Persona(
        "S6",
        "negotiation",
        ("Yes, 19,000, six hours, but send a 5,000 deposit on M-Pesa now.",),
        "Escalates; no agreement; ASK_USER with the deposit reason",
    ),
    "S7": Persona(
        "S7",
        "negotiation",
        ("Tell your client I said yes.", "18,000 for six hours."),
        "No agreement before an offer with a price is recorded; then agreement",
    ),
    "S8": Persona(
        "S8",
        "negotiation",
        ("Yes, 20,000 for six hours.", "Actually, make that 22,000.", "21,000 final."),
        "2 offers recorded before any counter; latest (22,000) triggers a counter; final ASK_USER",
    ),
    "S9": Persona(
        "S9",
        "negotiation",
        (
            "Yes free. It's twenty... [inaudible]",
            "Twenty-three thousand, six hours.",
            "Twenty-one thousand, final.",
        ),
        "The first record_offer with any amount is rejected/never made; no invented number stored",
    ),
    "S10": Persona(
        "S10",
        "negotiation",
        ("Sorry, I'm booked Monday.",),
        "Negotiation UNAVAILABLE; no offer amount",
    ),
    "S11": Persona(
        "S11",
        "negotiation",
        ("Yes, 19,000.", "I'll tell you the hours later."),
        "CLARIFY once, then MUST_ESCALATE incomplete_terms; ASK_USER",
    ),
    "S12": Persona(
        "S12",
        "confirmation",
        ("Yes, confirmed.",),
        "Negotiation CONFIRMED",
    ),
    "S13": Persona(
        "S13",
        "confirmation",
        ("Actually it's 22,000 now.",),
        "New confirmation_call offer 22,000; transaction back to re-approval (AWAITING_APPROVAL)",
    ),
}
