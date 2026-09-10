"""Africa's Talking Voice adapter for the TelephonyProvider interface.

Behaviour verified against AT's voice docs (docs.developers.africastalking.com, 2026-09-10):
- Outbound call: POST https://voice.africastalking.com/call with an `apiKey` header and form body
  `username`, `from`, `to`; the response carries `entries[].sessionId` and `status`.
- Every call event POSTs to the number's callback URL with `sessionId` and `isActive`;
  `isActive=0` is the final notification and its response is ignored.
- A partial `<Record>` wrapping a `<Say>` speaks, records, then POSTs `recordingUrl` to
  `callbackUrl`; the XML returned to that POST continues the call.
- No speech recognition (recordings go to the injected SpeechToText), no hang-up API (a `<Say>`
  with nothing after it ends the call), and no request signatures (the webhook path is secret).

Not yet verified on a live number: whether `timeout` ends a recording on silence, and whether
`recordingUrl` downloads need auth. A recording posted with nothing captured arrives as another
`isActive=1` event, which the CallCoordinator counts as silence.
"""

import logging
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

import httpx

from app.config import Settings
from app.conversation import AgentTurn
from app.speech.base import SpeechToText
from app.telephony.base import (
    CallAnswered,
    CallEnded,
    CallerSilent,
    CallerSpoke,
    CallEvent,
    PlacedCall,
    ProviderReply,
    TelephonyError,
)

log = logging.getLogger(__name__)

VOICE_CALL_URL = "https://voice.africastalking.com/call"
XML_MEDIA_TYPE = "application/xml"
XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>'
DEFAULT_RECORDING_HOSTS = ("africastalking.com", "at-internal.com")
MAX_RECORDING_BYTES = 5_000_000
GOODBYE = "Goodbye."


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except ValueError:
        return None


class AfricasTalkingVoiceProvider:
    def __init__(
        self,
        *,
        username: str,
        api_key: str,
        from_number: str,
        speech_to_text: SpeechToText,
        callback_url: str = "",
        tts_voice: str = "",
        record_max_seconds: int = 15,
        record_silence_timeout_seconds: int = 3,
        recording_hosts: Sequence[str] = DEFAULT_RECORDING_HOSTS,
        http_client: httpx.AsyncClient | None = None,
        call_url: str = VOICE_CALL_URL,
    ) -> None:
        self._username = username
        self._api_key = api_key
        self._from_number = from_number
        self._stt = speech_to_text
        self._callback_url = callback_url
        self._tts_voice = tts_voice
        self._record_max_seconds = record_max_seconds
        self._record_silence_timeout_seconds = record_silence_timeout_seconds
        self._recording_hosts = tuple(h.strip().lower() for h in recording_hosts if h.strip())
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._call_url = call_url

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # --- start a call ------------------------------------------------------------------------

    async def place_call(self, to_number: str) -> PlacedCall:
        if not (self._username and self._api_key and self._from_number):
            raise TelephonyError("AT_USERNAME, AT_API_KEY and AT_VOICE_NUMBER must be set")
        try:
            response = await self._http.post(
                self._call_url,
                headers={"apiKey": self._api_key, "Accept": "application/json"},
                data={"username": self._username, "from": self._from_number, "to": to_number},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise TelephonyError(f"Africa's Talking call request failed: HTTP {status}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise TelephonyError(f"Africa's Talking call request failed: {exc}") from exc

        entries = payload.get("entries") if isinstance(payload, dict) else None
        entry = entries[0] if entries else {}
        session_id, status = entry.get("sessionId"), entry.get("status")
        if status != "Queued" or not session_id or session_id == "None":
            error = payload.get("errorMessage") if isinstance(payload, dict) else None
            raise TelephonyError(f"Africa's Talking did not queue the call: {status} ({error})")
        return PlacedCall(provider_call_id=session_id)

    async def hang_up(self, provider_call_id: str) -> None:
        raise TelephonyError(
            "Africa's Talking has no hang-up API; end the call with AgentTurn(end_call=True)"
        )

    # --- callbacks ---------------------------------------------------------------------------

    async def parse_callback(self, form: Mapping[str, str]) -> CallEvent:
        session_id = form.get("sessionId", "").strip()
        if not session_id:
            raise TelephonyError("Africa's Talking callback without sessionId")

        if form.get("isActive") == "0":
            return CallEnded(
                session_id,
                reason=form.get("hangupCause") or form.get("callSessionState") or None,
                duration_seconds=_int_or_none(form.get("durationInSeconds")),
            )
        if recording_url := form.get("recordingUrl", "").strip():
            text = await self._transcribe(session_id, recording_url)
            return CallerSpoke(session_id, text) if text else CallerSilent(session_id)
        if digits := form.get("dtmfDigits", "").strip():
            return CallerSpoke(session_id, digits)

        outbound = form.get("direction", "").lower() == "outbound"
        remote = form.get("destinationNumber" if outbound else "callerNumber") or None
        return CallAnswered(session_id, remote)

    async def _transcribe(self, session_id: str, url: str) -> str:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        allowed = any(host == h or host.endswith("." + h) for h in self._recording_hosts)
        if parts.scheme not in ("http", "https") or not allowed:
            log.warning("session %s: refusing recording URL on host %r", session_id, host)
            return ""

        audio = bytearray()
        try:
            async with self._http.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "audio/mpeg")
                async for chunk in response.aiter_bytes():
                    audio.extend(chunk)
                    if len(audio) > MAX_RECORDING_BYTES:
                        log.warning("session %s: recording over %d bytes", session_id, len(audio))
                        return ""
        except httpx.HTTPError as exc:
            log.warning("session %s: recording download failed: %s", session_id, exc)
            return ""
        if not audio:
            return ""

        try:
            text = await self._stt.transcribe(
                bytes(audio), content_type=content_type.split(";")[0].strip()
            )
        except Exception:  # a failed transcription is a missed turn, not a dropped call
            log.exception("session %s: speech-to-text failed", session_id)
            return ""
        return text.strip()

    # --- speak / collect input / hang up -------------------------------------------------------

    def render(self, turn: AgentTurn) -> ProviderReply:
        root = ET.Element("Response")
        if turn.end_call:
            self._say(root, turn.say or GOODBYE)  # nothing after <Say>: AT hangs up
        else:
            record = ET.SubElement(root, "Record", self._record_attributes(prompt=bool(turn.say)))
            if turn.say:
                self._say(record, turn.say)
        return self._reply(root)

    def acknowledge(self) -> ProviderReply:
        return self._reply(ET.Element("Response"))

    def _record_attributes(self, *, prompt: bool) -> dict[str, str]:
        attributes = {
            "finishOnKey": "#",
            "maxLength": str(self._record_max_seconds),
            "timeout": str(self._record_silence_timeout_seconds),
            "trimSilence": "true",
            "playBeep": "false" if prompt else "true",  # beep only when nothing was said
        }
        if self._callback_url:
            attributes["callbackUrl"] = self._callback_url
        return attributes

    def _say(self, parent: ET.Element, text: str) -> None:
        say = ET.SubElement(parent, "Say", {"voice": self._tts_voice} if self._tts_voice else {})
        say.text = text

    @staticmethod
    def _reply(root: ET.Element) -> ProviderReply:
        return ProviderReply(
            XML_DECLARATION + ET.tostring(root, encoding="unicode"), XML_MEDIA_TYPE
        )


def build_africastalking_provider(
    settings: Settings,
    speech_to_text: SpeechToText,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> AfricasTalkingVoiceProvider:
    callback_url = ""
    if settings.webhook_base_url and settings.voice_webhook_secret:
        base = settings.webhook_base_url.rstrip("/")
        callback_url = f"{base}/webhooks/voice/{settings.voice_webhook_secret}"
    return AfricasTalkingVoiceProvider(
        username=settings.at_username,
        api_key=settings.at_api_key,
        from_number=settings.at_voice_number,
        speech_to_text=speech_to_text,
        callback_url=callback_url,
        tts_voice=settings.at_tts_voice,
        record_max_seconds=settings.at_record_max_seconds,
        record_silence_timeout_seconds=settings.at_record_silence_timeout_seconds,
        recording_hosts=settings.at_recording_hosts.split(","),
        http_client=http_client,
    )
