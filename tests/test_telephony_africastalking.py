"""Africa's Talking boundary tests: no network, no credentials, no phone number.

Callback forms use the field names from AT's voice notification docs.
"""

from urllib.parse import parse_qsl

import httpx
import pytest

from app.calls import DIDNT_CATCH, CallCoordinator
from app.config import Settings
from app.conversation import AgentTurn, CallerMessage
from app.speech.base import SpeechToTextError
from app.speech.fake import FakeSpeechToText
from app.telephony.africastalking import (
    VOICE_CALL_URL,
    XML_DECLARATION,
    AfricasTalkingVoiceProvider,
    build_africastalking_provider,
)
from app.telephony.base import (
    CallAnswered,
    CallEnded,
    CallerSilent,
    CallerSpoke,
    PlacedCall,
    TelephonyError,
)

CALLBACK = "https://tunnel.example/webhooks/voice/s3cret"
RECORDING = "https://voice.africastalking.com/recordings/abc.mp3"
FROM_NUMBER = "+254200000000"
PROVIDER_PHONE = "+254100000001"


class Http:
    """httpx MockTransport that records requests and answers AT-shaped responses."""

    def __init__(
        self, recording_status=200, audio=b"ID3-fake-mp3", call_json=None, call_status=200
    ):
        self.requests: list[httpx.Request] = []
        self.recording_status = recording_status
        self.audio = audio
        self.call_json = call_json or {
            "entries": [
                {"phoneNumber": PROVIDER_PHONE, "status": "Queued", "sessionId": "ATVId_1"}
            ],
            "errorMessage": "None",
        }
        self.call_status = call_status

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                self.recording_status, content=self.audio, headers={"content-type": "audio/mpeg"}
            )
        if isinstance(self.call_json, str):
            return httpx.Response(self.call_status, text=self.call_json)
        return httpx.Response(self.call_status, json=self.call_json)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def make(http: Http | None = None, stt: FakeSpeechToText | None = None, **kwargs):
    http = http or Http()
    stt = stt if stt is not None else FakeSpeechToText("Yes, I'm free. 23,000 for six hours.")
    options = {
        "username": "atuser",
        "api_key": "at-test-key",
        "from_number": FROM_NUMBER,
        "speech_to_text": stt,
        "callback_url": CALLBACK,
        "http_client": http.client(),
    } | kwargs
    return AfricasTalkingVoiceProvider(**options), http, stt


def form(**fields) -> dict[str, str]:
    base = {
        "sessionId": "ATVId_1",
        "isActive": "1",
        "direction": "Outbound",
        "callerNumber": FROM_NUMBER,
        "destinationNumber": PROVIDER_PHONE,
    }
    return base | fields


# --- render ------------------------------------------------------------------------------------


def test_render_speaks_then_records_with_escaped_text():
    provider, _, _ = make()

    reply = provider.render(AgentTurn("Is KES 20,000 & 6 hours <ok>?"))

    assert reply.media_type == "application/xml"
    assert reply.body == (
        XML_DECLARATION + "<Response>"
        '<Record finishOnKey="#" maxLength="15" timeout="3" trimSilence="true" playBeep="false" '
        f'callbackUrl="{CALLBACK}">'
        "<Say>Is KES 20,000 &amp; 6 hours &lt;ok&gt;?</Say>"
        "</Record></Response>"
    )


def test_render_end_call_says_and_stops_so_at_hangs_up():
    provider, _, _ = make()
    reply = provider.render(AgentTurn("I'll take that to my client. Goodbye.", end_call=True))
    assert reply.body == (
        XML_DECLARATION + "<Response><Say>I'll take that to my client. Goodbye.</Say></Response>"
    )


def test_render_end_call_without_words_still_says_goodbye():
    provider, _, _ = make()
    assert "<Say>Goodbye.</Say>" in provider.render(AgentTurn("", end_call=True)).body


def test_render_without_words_records_after_a_beep():
    provider, _, _ = make()
    body = provider.render(AgentTurn("")).body
    assert 'playBeep="true"' in body and "<Say" not in body


def test_render_uses_configured_tts_voice_and_record_limits():
    provider, _, _ = make(tts_voice="en-US-Standard-C", record_max_seconds=20, callback_url="")
    body = provider.render(AgentTurn("Hello")).body
    assert '<Say voice="en-US-Standard-C">Hello</Say>' in body
    assert 'maxLength="20"' in body
    assert "callbackUrl" not in body


def test_acknowledge_is_empty_response():
    provider, _, _ = make()
    assert provider.acknowledge().body == XML_DECLARATION + "<Response />"


# --- parse_callback ----------------------------------------------------------------------------


async def test_first_active_callback_is_answered_outbound_remote_is_destination():
    provider, _, _ = make()
    assert await provider.parse_callback(form()) == CallAnswered("ATVId_1", PROVIDER_PHONE)


async def test_inbound_remote_is_caller():
    provider, _, _ = make()
    event = await provider.parse_callback(
        form(direction="Inbound", callerNumber=PROVIDER_PHONE, destinationNumber=FROM_NUMBER)
    )
    assert event == CallAnswered("ATVId_1", PROVIDER_PHONE)


async def test_final_notification_is_call_ended():
    provider, _, _ = make()
    event = await provider.parse_callback(
        form(isActive="0", hangupCause="NORMAL_CLEARING", durationInSeconds="47", amount="2.5")
    )
    assert event == CallEnded("ATVId_1", "NORMAL_CLEARING", 47)


async def test_final_notification_without_duration():
    provider, _, _ = make()
    event = await provider.parse_callback(form(isActive="0", callSessionState="Completed"))
    assert event == CallEnded("ATVId_1", "Completed", None)


async def test_recording_is_downloaded_and_transcribed():
    provider, http, stt = make()

    event = await provider.parse_callback(form(recordingUrl=RECORDING))

    assert event == CallerSpoke("ATVId_1", "Yes, I'm free. 23,000 for six hours.")
    (request,) = http.requests
    assert (request.method, str(request.url)) == ("GET", RECORDING)
    assert "apikey" not in request.headers  # never send the AT key to a URL taken from a callback
    assert stt.received == [(b"ID3-fake-mp3", "audio/mpeg")]


async def test_empty_transcript_is_silence():
    provider, _, _ = make(stt=FakeSpeechToText(""))
    assert await provider.parse_callback(form(recordingUrl=RECORDING)) == CallerSilent("ATVId_1")


async def test_recording_download_failure_is_silence():
    provider, _, stt = make(Http(recording_status=404))
    assert await provider.parse_callback(form(recordingUrl=RECORDING)) == CallerSilent("ATVId_1")
    assert stt.received == []


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/rec.mp3",
        "https://africastalking.com.evil.example/rec.mp3",
        "file:///etc/passwd",
        "http://127.0.0.1:8000/transactions",
    ],
)
async def test_recording_on_unexpected_host_is_refused_without_fetching(url):
    provider, http, _ = make()
    assert await provider.parse_callback(form(recordingUrl=url)) == CallerSilent("ATVId_1")
    assert http.requests == []


async def test_oversized_recording_is_silence():
    provider, _, stt = make(Http(audio=b"x" * 5_000_001))
    assert await provider.parse_callback(form(recordingUrl=RECORDING)) == CallerSilent("ATVId_1")
    assert stt.received == []


async def test_speech_to_text_error_is_silence():
    provider, _, _ = make(stt=FakeSpeechToText(error=SpeechToTextError("quota")))
    assert await provider.parse_callback(form(recordingUrl=RECORDING)) == CallerSilent("ATVId_1")


async def test_dtmf_digits_are_caller_input():
    provider, _, _ = make()
    assert await provider.parse_callback(form(dtmfDigits="1")) == CallerSpoke("ATVId_1", "1")


async def test_callback_without_session_id_is_rejected():
    provider, _, _ = make()
    with pytest.raises(TelephonyError, match="sessionId"):
        await provider.parse_callback({"isActive": "1"})


# --- place_call / hang_up ----------------------------------------------------------------------


async def test_place_call_posts_documented_request_and_returns_session():
    provider, http, _ = make()

    assert await provider.place_call(PROVIDER_PHONE) == PlacedCall("ATVId_1")

    (request,) = http.requests
    assert (request.method, str(request.url)) == ("POST", VOICE_CALL_URL)
    assert request.headers["apiKey"] == "at-test-key"
    assert dict(parse_qsl(request.content.decode())) == {
        "username": "atuser",
        "from": FROM_NUMBER,
        "to": PROVIDER_PHONE,
    }


@pytest.mark.parametrize(
    ("http", "message"),
    [
        (Http(call_json={"entries": [{"status": "InsufficientCredit", "sessionId": "None"}],
                         "errorMessage": "Insufficient credit"}), "InsufficientCredit"),
        (Http(call_json={"entries": [], "errorMessage": "Invalid callerId"}), "Invalid callerId"),
        (Http(call_status=401, call_json={"error": "unauthorized"}), "HTTP 401"),
        (Http(call_json="<html>gateway error</html>"), "call request failed"),
    ],
)  # fmt: skip
async def test_place_call_failures_raise_telephony_error(http, message):
    provider, _, _ = make(http)
    with pytest.raises(TelephonyError, match=message):
        await provider.place_call(PROVIDER_PHONE)


async def test_place_call_requires_credentials_before_any_request():
    provider, http, _ = make(api_key="")
    with pytest.raises(TelephonyError, match="AT_API_KEY"):
        await provider.place_call(PROVIDER_PHONE)
    assert http.requests == []


async def test_hang_up_is_not_supported_by_africas_talking():
    provider, _, _ = make()
    with pytest.raises(TelephonyError, match="no hang-up API"):
        await provider.hang_up("ATVId_1")


def test_factory_builds_callback_url_from_settings():
    settings = Settings(
        _env_file=None,
        at_username="atuser",
        at_api_key="k",
        at_voice_number=FROM_NUMBER,
        webhook_base_url="https://tunnel.example/",
        voice_webhook_secret="s3cret",
        at_recording_hosts="africastalking.com, at-internal.com",
    )
    provider = build_africastalking_provider(settings, FakeSpeechToText())
    assert f'callbackUrl="{CALLBACK}"' in provider.render(AgentTurn("Hi")).body


def test_factory_omits_callback_url_without_secret():
    settings = Settings(_env_file=None, webhook_base_url="https://tunnel.example")
    provider = build_africastalking_provider(settings, FakeSpeechToText())
    assert "callbackUrl" not in provider.render(AgentTurn("Hi")).body


# --- whole call through the coordinator --------------------------------------------------------


class EchoAgent:
    def __init__(self) -> None:
        self.heard: list[str] = []
        self.finished: list[str | None] = []

    async def start(self) -> AgentTurn:
        return AgentTurn("Hi, I'm an AI assistant calling for a client. Are you free Monday?")

    async def respond(self, message: CallerMessage) -> AgentTurn:
        self.heard.append(message.text)
        return AgentTurn("Thanks. I'll take that to my client. Goodbye.", end_call=True)

    async def finish(self, reason: str | None) -> None:
        self.finished.append(reason)


async def test_africas_talking_call_flow_through_coordinator():
    provider, _, _ = make()
    agent = EchoAgent()
    coordinator = CallCoordinator(provider, lambda _sid: agent)

    greeting = await coordinator.handle_callback(form())
    empty_recording = await coordinator.handle_callback(form())  # recording captured nothing
    answer = await coordinator.handle_callback(form(recordingUrl=RECORDING))
    done = await coordinator.handle_callback(form(isActive="0", hangupCause="NORMAL_CLEARING"))

    assert "<Record" in greeting.body and "Are you free Monday?" in greeting.body
    assert f"<Say>{DIDNT_CATCH}</Say>" in empty_recording.body
    assert agent.heard == ["Yes, I'm free. 23,000 for six hours."]
    assert answer.body.endswith(
        "<Say>Thanks. I'll take that to my client. Goodbye.</Say></Response>"
    )
    assert "<Record" not in answer.body
    assert done == provider.acknowledge()
    assert agent.finished == ["NORMAL_CLEARING"]
