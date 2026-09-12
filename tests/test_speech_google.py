"""GoogleSpeechToText: offline tests against a stubbed client, plus an opt-in live test."""

import os
from pathlib import Path

import pytest
from google.api_core import exceptions as google_exceptions
from google.auth.exceptions import DefaultCredentialsError
from google.cloud.speech_v2.types import cloud_speech

from app.config import Settings
from app.speech.base import SpeechToTextError
from app.speech.google import (
    MAX_INLINE_BYTES,
    PHRASE_BOOST,
    GoogleSpeechToText,
    build_google_speech_to_text,
    endpoint_for,
)
from app.telephony.africastalking import AfricasTalkingVoiceProvider
from app.telephony.base import CallerSpoke

AUDIO = b"ID3-fake-mp3"


class StubClient:
    def __init__(self, response=None, error: Exception | None = None):
        self.requests: list[cloud_speech.RecognizeRequest] = []
        self.timeouts: list[float] = []
        # An empty RecognizeResponse is falsy, so test for None explicitly.
        default = recognize_response("Yes, twenty-three thousand.")
        self._response = default if response is None else response
        self._error = error

    async def recognize(self, *, request, timeout):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self._error:
            raise self._error
        return self._response


def recognize_response(*transcripts: str | None) -> cloud_speech.RecognizeResponse:
    results = [
        cloud_speech.SpeechRecognitionResult(
            alternatives=[]
            if t is None
            else [cloud_speech.SpeechRecognitionAlternative(transcript=t)]
        )
        for t in transcripts
    ]
    return cloud_speech.RecognizeResponse(results=results)


class Factory:
    def __init__(self, client=None, errors: list[Exception] | None = None):
        self.client = client or StubClient()
        self.errors = list(errors or [])
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.client


def stt(factory: Factory | None = None, **kwargs) -> tuple[GoogleSpeechToText, Factory]:
    factory = factory or Factory()
    return GoogleSpeechToText(project="demo-project", client_factory=factory, **kwargs), factory


def has(message, field: str) -> bool:
    return type(message).pb(message).HasField(field)


async def test_request_uses_implicit_recognizer_chirp3_autodecoding_and_timeout():
    speech, factory = stt()

    text = await speech.transcribe(AUDIO, content_type="audio/mpeg")

    assert text == "Yes, twenty-three thousand."
    (request,) = factory.client.requests
    assert request.recognizer == "projects/demo-project/locations/eu/recognizers/_"
    assert request.content == AUDIO
    assert request.config.model == "chirp_3"
    assert list(request.config.language_codes) == ["en-GB"]
    assert has(request.config, "auto_decoding_config")
    assert request.config.features.enable_automatic_punctuation is True
    assert not has(request.config, "adaptation")
    assert factory.client.timeouts == [8.0]


async def test_phrase_hints_become_inline_adaptation():
    speech, factory = stt(phrase_hints=["shillings", "KES"], language_codes=["en-US", "en-IN"])

    await speech.transcribe(AUDIO, content_type="audio/mpeg")

    config = factory.client.requests[0].config
    assert list(config.language_codes) == ["en-US", "en-IN"]
    (phrase_set,) = config.adaptation.phrase_sets
    assert [(p.value, p.boost) for p in phrase_set.inline_phrase_set.phrases] == [
        ("shillings", PHRASE_BOOST),
        ("KES", PHRASE_BOOST),
    ]


async def test_results_are_joined_and_empty_ones_skipped():
    client = StubClient(recognize_response("Yes, I'm free.", None, "  ", " 23,000 for six hours. "))
    speech, _ = stt(Factory(client))

    assert await speech.transcribe(AUDIO, content_type="audio/mpeg") == (
        "Yes, I'm free. 23,000 for six hours."
    )


async def test_no_speech_returns_empty_string():
    speech, _ = stt(Factory(StubClient(recognize_response())))
    assert await speech.transcribe(AUDIO, content_type="audio/mpeg") == ""


async def test_empty_audio_never_creates_a_client():
    speech, factory = stt()
    assert await speech.transcribe(b"", content_type="audio/mpeg") == ""
    assert factory.calls == 0


async def test_oversized_audio_is_rejected_before_any_request():
    speech, factory = stt()
    with pytest.raises(SpeechToTextError, match="10 MB"):
        await speech.transcribe(b"x" * (MAX_INLINE_BYTES + 1), content_type="audio/mpeg")
    assert factory.calls == 0


async def test_client_is_created_once_and_reused():
    speech, factory = stt()
    await speech.transcribe(AUDIO, content_type="audio/mpeg")
    await speech.transcribe(AUDIO, content_type="audio/mpeg")
    assert factory.calls == 1


async def test_missing_credentials_raise_speech_error_and_retry_next_turn():
    factory = Factory(errors=[DefaultCredentialsError("no ADC")])
    speech, _ = stt(factory)

    with pytest.raises(SpeechToTextError, match="credentials"):
        await speech.transcribe(AUDIO, content_type="audio/mpeg")
    assert (
        await speech.transcribe(AUDIO, content_type="audio/mpeg") == "Yes, twenty-three thousand."
    )


async def test_missing_service_account_file_is_a_speech_error_without_network():
    speech = GoogleSpeechToText(project="demo-project", credentials_file="/nonexistent/key.json")
    with pytest.raises(SpeechToTextError, match="credentials"):
        await speech.transcribe(AUDIO, content_type="audio/mpeg")


@pytest.mark.parametrize(
    "error",
    [
        google_exceptions.InvalidArgument("bad audio"),
        google_exceptions.PermissionDenied("no role"),
        google_exceptions.ResourceExhausted("quota"),
        google_exceptions.DeadlineExceeded("slow"),
        google_exceptions.ServiceUnavailable("down"),
        google_exceptions.RetryError("gave up", cause=None),
    ],
)
async def test_google_api_errors_become_speech_errors(error):
    speech, _ = stt(Factory(StubClient(error=error)))
    with pytest.raises(SpeechToTextError, match=type(error).__name__):
        await speech.transcribe(AUDIO, content_type="audio/mpeg")


def test_configuration_errors_fail_fast():
    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
        GoogleSpeechToText(project="")
    with pytest.raises(ValueError, match="GOOGLE_STT_LANGUAGE_CODES"):
        GoogleSpeechToText(project="p", language_codes=[])


@pytest.mark.parametrize(
    ("location", "endpoint"),
    [
        ("eu", "eu-speech.googleapis.com"),
        ("us", "us-speech.googleapis.com"),
        ("global", None),
        ("", None),
    ],
)
def test_endpoint_for(location, endpoint):
    assert endpoint_for(location) == endpoint


async def test_factory_reads_settings():
    settings = Settings(
        _env_file=None,
        google_cloud_project="proj",
        google_stt_location="us",
        google_stt_model="chirp_3",
        google_stt_language_codes="en-GB, en-US",
        google_stt_phrase_hints="shillings, ,KES",
        google_stt_timeout_seconds=5,
    )
    factory = Factory()
    speech = build_google_speech_to_text(settings, client_factory=factory)

    await speech.transcribe(AUDIO, content_type="audio/mpeg")

    (request,) = factory.client.requests
    assert request.recognizer == "projects/proj/locations/us/recognizers/_"
    assert list(request.config.language_codes) == ["en-GB", "en-US"]
    phrases = request.config.adaptation.phrase_sets[0].inline_phrase_set.phrases
    assert [p.value for p in phrases] == ["shillings", "KES"]
    assert factory.client.timeouts == [5.0]


async def test_plugs_into_africas_talking_adapter():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=AUDIO, headers={"content-type": "audio/mpeg"})

    speech, _ = stt()
    provider = AfricasTalkingVoiceProvider(
        username="u",
        api_key="k",
        from_number="+254200000000",
        speech_to_text=speech,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    event = await provider.parse_callback(
        {
            "sessionId": "ATVId_1",
            "isActive": "1",
            "recordingUrl": "https://voice.africastalking.com/r.mp3",
        }
    )

    assert event == CallerSpoke("ATVId_1", "Yes, twenty-three thousand.")


# --- live: real Google Speech-to-Text, opt in with `uv run pytest -m live` ---------------------


@pytest.mark.live
async def test_live_transcribes_local_test_audio():
    settings = Settings()
    audio_path = os.environ.get("GOOGLE_STT_TEST_AUDIO", "")
    if not (settings.google_cloud_project and audio_path and Path(audio_path).is_file()):
        pytest.skip("set GOOGLE_CLOUD_PROJECT, credentials and GOOGLE_STT_TEST_AUDIO=<audio file>")
    speech = build_google_speech_to_text(settings)

    text = await speech.transcribe(Path(audio_path).read_bytes(), content_type="audio/mpeg")

    assert text.strip()
