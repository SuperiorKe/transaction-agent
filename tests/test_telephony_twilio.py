"""Twilio Voice boundary tests: all mocked, with no account or phone number required."""

from urllib.parse import parse_qsl

import httpx
import pytest

from app.config import Settings
from app.conversation import AgentTurn
from app.telephony.base import (
    CallAnswered,
    CallEnded,
    CallerSilent,
    CallerSpoke,
    PlacedCall,
    TelephonyError,
)
from app.telephony.twilio import (
    TWILIO_API_BASE_URL,
    XML_DECLARATION,
    TwilioVoiceProvider,
    build_twilio_provider,
)

CALLBACK = "https://tunnel.example/webhooks/voice/s3cret"
FROM_NUMBER = "+254200000000"
PROVIDER_PHONE = "+254100000001"
ACCOUNT_SID = "ACtest"


class Http:
    def __init__(self, *, status: int = 201, payload: object = None) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.payload = {"sid": "CA123"} if payload is None else payload

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.payload, str):
            return httpx.Response(self.status, text=self.payload)
        return httpx.Response(self.status, json=self.payload)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def make(http: Http | None = None, **kwargs):
    http = http or Http()
    options = {
        "account_sid": ACCOUNT_SID,
        "auth_token": "token",
        "from_number": FROM_NUMBER,
        "callback_url": CALLBACK,
        "http_client": http.client(),
    } | kwargs
    return TwilioVoiceProvider(**options), http


def form(**fields: str) -> dict[str, str]:
    return {
        "CallSid": "CA123",
        "CallStatus": "in-progress",
        "Direction": "outbound-api",
        "From": FROM_NUMBER,
        "To": PROVIDER_PHONE,
    } | fields


def test_render_uses_speech_gather_and_escapes_text():
    provider, _ = make()
    reply = provider.render(AgentTurn("Is KES 20,000 & 6 hours <ok>?"))

    assert reply.media_type == "application/xml"
    assert reply.body == (
        XML_DECLARATION + "<Response>"
        '<Gather input="speech" speechTimeout="auto" actionOnEmptyResult="true" '
        f'action="{CALLBACK}" method="POST">'
        "<Say>Is KES 20,000 &amp; 6 hours &lt;ok&gt;?</Say>"
        "</Gather></Response>"
    )


def test_render_end_call_says_goodbye_without_gather():
    provider, _ = make()
    body = provider.render(AgentTurn("", end_call=True)).body
    assert "<Say>Goodbye.</Say>" in body
    assert "<Gather" not in body


def test_render_without_callback_still_collects_speech():
    provider, _ = make(callback_url="")
    body = provider.render(AgentTurn("Hello")).body
    assert '<Gather input="speech" speechTimeout="auto" actionOnEmptyResult="true">' in body
    assert " action=" not in body


def test_acknowledge_is_an_empty_twiml_response():
    provider, _ = make()
    assert provider.acknowledge().body == XML_DECLARATION + "<Response />"


async def test_initial_callback_is_answered_and_uses_outbound_destination():
    provider, _ = make()
    assert await provider.parse_callback(form()) == CallAnswered("CA123", PROVIDER_PHONE)


async def test_inbound_callback_uses_caller_as_remote_number():
    provider, _ = make()
    event = await provider.parse_callback(
        form(Direction="inbound", From=PROVIDER_PHONE, To=FROM_NUMBER)
    )
    assert event == CallAnswered("CA123", PROVIDER_PHONE)


async def test_speech_result_becomes_caller_spoke_even_with_low_confidence():
    provider, _ = make()
    event = await provider.parse_callback(
        form(SpeechResult=" 23,000 for six hours ", Confidence="0.41")
    )
    assert event == CallerSpoke("CA123", "23,000 for six hours")


async def test_empty_speech_result_is_silence():
    provider, _ = make()
    event = await provider.parse_callback(form(SpeechResult="", Confidence=""))
    assert event == CallerSilent("CA123")


@pytest.mark.parametrize("status", ["completed", "busy", "failed", "no-answer", "canceled"])
async def test_terminal_status_is_call_ended(status):
    provider, _ = make()
    assert await provider.parse_callback(form(CallStatus=status, CallDuration="47")) == CallEnded(
        "CA123", status, 47
    )


async def test_callback_without_call_sid_is_rejected():
    provider, _ = make()
    with pytest.raises(TelephonyError, match="CallSid"):
        await provider.parse_callback({"CallStatus": "in-progress"})


async def test_place_call_posts_twilio_request_and_returns_call_sid():
    provider, http = make()
    assert await provider.place_call(PROVIDER_PHONE) == PlacedCall("CA123")

    (request,) = http.requests
    assert (request.method, str(request.url)) == (
        "POST",
        f"{TWILIO_API_BASE_URL}/Accounts/{ACCOUNT_SID}/Calls.json",
    )
    assert dict(parse_qsl(request.content.decode())) == {
        "To": PROVIDER_PHONE,
        "From": FROM_NUMBER,
        "Url": CALLBACK,
        "Method": "POST",
        "StatusCallback": CALLBACK,
        "StatusCallbackMethod": "POST",
        "StatusCallbackEvent": "completed",
    }
    assert request.headers["authorization"].startswith("Basic ")


@pytest.mark.parametrize(
    ("http", "message"),
    [
        (Http(status=401, payload={"message": "Authenticate"}), "HTTP 401"),
        (Http(payload={"message": "Bad number"}), "did not create"),
        (Http(payload="<html>gateway error</html>"), "call request failed"),
    ],
)
async def test_place_call_failures_raise_telephony_error(http, message):
    provider, _ = make(http)
    with pytest.raises(TelephonyError, match=message):
        await provider.place_call(PROVIDER_PHONE)


async def test_place_call_requires_credentials_and_callback_before_request():
    provider, http = make(auth_token="")
    with pytest.raises(TelephonyError, match="TWILIO_AUTH_TOKEN"):
        await provider.place_call(PROVIDER_PHONE)
    assert http.requests == []

    provider, http = make(callback_url="")
    with pytest.raises(TelephonyError, match="WEBHOOK_BASE_URL"):
        await provider.place_call(PROVIDER_PHONE)
    assert http.requests == []


async def test_hang_up_posts_completed_status():
    provider, http = make()
    await provider.hang_up("CA456")
    (request,) = http.requests
    assert str(request.url).endswith(f"/Accounts/{ACCOUNT_SID}/Calls/CA456.json")
    assert dict(parse_qsl(request.content.decode())) == {"Status": "completed"}


def test_factory_builds_callback_url_from_settings():
    settings = Settings(
        _env_file=None,
        twilio_account_sid=ACCOUNT_SID,
        twilio_auth_token="token",
        twilio_voice_number=FROM_NUMBER,
        webhook_base_url="https://tunnel.example/",
        voice_webhook_secret="s3cret",
    )
    provider = build_twilio_provider(settings)
    assert f'action="{CALLBACK}"' in provider.render(AgentTurn("Hi")).body
