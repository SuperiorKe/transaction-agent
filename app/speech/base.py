"""Speech-to-text interface.

Needed because some telephony providers (Africa's Talking Voice) deliver the caller's turn as a
recording, not text. The concrete provider is a deployment choice; tests use `speech.fake`.
"""

from typing import Protocol


class SpeechToTextError(Exception):
    pass


class SpeechToText(Protocol):
    async def transcribe(self, audio: bytes, *, content_type: str) -> str:
        """Return the transcript of `audio`, or "" if nothing intelligible was said."""
        ...
