"""Scripted speech-to-text for offline tests: returns queued transcripts, records audio received."""

from collections import deque
from collections.abc import Iterable


class FakeSpeechToText:
    def __init__(self, transcripts: str | Iterable[str] = "", *, error: Exception | None = None):
        items = [transcripts] if isinstance(transcripts, str) else list(transcripts)
        self._transcripts = deque(items)
        self._error = error
        self.received: list[tuple[bytes, str]] = []

    async def transcribe(self, audio: bytes, *, content_type: str) -> str:
        self.received.append((audio, content_type))
        if self._error is not None:
            raise self._error
        return self._transcripts.popleft() if self._transcripts else ""
