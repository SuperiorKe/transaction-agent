"""Twilio Voice adapter for the :class:`TelephonyProvider` interface.

Twilio's ``<Gather input=\"speech\">`` performs speech recognition during the call and
POSTs ``SpeechResult`` (and ``Confidence``) to its action URL.  This deliberately avoids
recording downloads and a separate speech-to-text account.
"""

import xml.etree.ElementTree as ET
from collections.abc import Mapping

import httpx

from app.config import Settings
from app.conversation import AgentTurn
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

TWILIO_API_BASE_URL = "https://api.twilio.com/2010-04-01"
XML_MEDIA_TYPE = "application/xml"
XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>'
GOODBYE = "Goodbye."
TERMINAL_STATUSES = frozenset({"completed", "busy", "failed", "no-answer", "canceled"})


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except (ValueError, OverflowError):
        return None


def _float_or_none(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (ValueError, OverflowError):
        return None


class TwilioVoiceProvider:
    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        callback_url: str = "",
        http_client: httpx.AsyncClient | None = None,
        api_base_url: str = TWILIO_API_BASE_URL,
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number
        self._callback_url = callback_url
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._api_base_url = api_base_url.rstrip("/")

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def place_call(self, to_number: str) -> PlacedCall:
        if not (self._account_sid and self._auth_token and self._from_number):
            raise TelephonyError(
                "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_VOICE_NUMBER must be set"
            )
        if not self._callback_url:
            raise TelephonyError("WEBHOOK_BASE_URL and VOICE_WEBHOOK_SECRET must be set")

        data = {
            "To": to_number,
            "From": self._from_number,
            "Url": self._callback_url,
            "Method": "POST",
            # The initial Url request starts the conversation; this only reports the final
            # status to the same secret endpoint, avoiding duplicate ringing callbacks.
            "StatusCallback": self._callback_url,
            "StatusCallbackMethod": "POST",
            "StatusCallbackEvent": "completed",
        }
        try:
            response = await self._http.post(
                f"{self._api_base_url}/Accounts/{self._account_sid}/Calls.json",
                auth=(self._account_sid, self._auth_token),
                data=data,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise TelephonyError(
                f"Twilio call request failed: HTTP {exc.response.status_code}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise TelephonyError(f"Twilio call request failed: {exc}") from exc

        call_sid = payload.get("sid") if isinstance(payload, dict) else None
        if not call_sid:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise TelephonyError(f"Twilio did not create the call: {message or 'missing sid'}")
        return PlacedCall(provider_call_id=call_sid)

    async def hang_up(self, provider_call_id: str) -> None:
        if not (self._account_sid and self._auth_token):
            raise TelephonyError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set")
        try:
            response = await self._http.post(
                f"{self._api_base_url}/Accounts/{self._account_sid}/Calls/{provider_call_id}.json",
                auth=(self._account_sid, self._auth_token),
                data={"Status": "completed"},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TelephonyError(f"Twilio hang-up failed: HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise TelephonyError(f"Twilio hang-up failed: {exc}") from exc

    async def parse_callback(self, form: Mapping[str, str]) -> CallEvent:
        call_sid = form.get("CallSid", "").strip()
        if not call_sid:
            raise TelephonyError("Twilio callback without CallSid")

        status = form.get("CallStatus", "").strip().lower()
        if status in TERMINAL_STATUSES:
            return CallEnded(
                call_sid,
                reason=status or None,
                duration_seconds=_int_or_none(form.get("CallDuration") or form.get("Duration")),
            )

        # Confidence is intentionally parsed from Twilio's form but is not used as a hard
        # threshold: a low score still gives the conversation agent a chance to clarify.
        _confidence = _float_or_none(form.get("Confidence", "").strip())
        if "SpeechResult" in form:
            speech = form.get("SpeechResult", "").strip()
            return CallerSpoke(call_sid, speech) if speech else CallerSilent(call_sid)

        direction = form.get("Direction", "").lower()
        remote = form.get("To" if direction == "outbound-api" else "From") or None
        return CallAnswered(call_sid, remote)

    def render(self, turn: AgentTurn) -> ProviderReply:
        root = ET.Element("Response")
        if turn.end_call:
            self._say(root, turn.say or GOODBYE)
        else:
            attributes = {
                "input": "speech",
                "speechTimeout": "auto",
                "actionOnEmptyResult": "true",
            }
            if self._callback_url:
                attributes.update({"action": self._callback_url, "method": "POST"})
            gather = ET.SubElement(root, "Gather", attributes)
            if turn.say:
                self._say(gather, turn.say)
        return self._reply(root)

    def acknowledge(self) -> ProviderReply:
        return self._reply(ET.Element("Response"))

    @staticmethod
    def _say(parent: ET.Element, text: str) -> None:
        say = ET.SubElement(parent, "Say")
        say.text = text

    @staticmethod
    def _reply(root: ET.Element) -> ProviderReply:
        return ProviderReply(
            XML_DECLARATION + ET.tostring(root, encoding="unicode"), XML_MEDIA_TYPE
        )


def build_twilio_provider(
    settings: Settings, *, http_client: httpx.AsyncClient | None = None
) -> TwilioVoiceProvider:
    callback_url = ""
    if settings.webhook_base_url and settings.voice_webhook_secret:
        callback_url = (
            f"{settings.webhook_base_url.rstrip('/')}/webhooks/voice/"
            f"{settings.voice_webhook_secret}"
        )
    return TwilioVoiceProvider(
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token,
        from_number=settings.twilio_voice_number,
        callback_url=callback_url,
        http_client=http_client,
    )
