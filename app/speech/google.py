"""Google Cloud Speech-to-Text v2 adapter for the SpeechToText interface.

Verified 2026-09-11 against google-cloud-speech 2.40.0 and Google's v2 docs:
- `SpeechAsyncClient.recognize(RecognizeRequest(recognizer, config, content))` with the implicit
  recognizer `projects/{project}/locations/{location}/recognizers/_`.
- `AutoDetectDecodingConfig` accepts MP3 and WAV, so Africa's Talking recordings go in as-is
  (`content_type` is therefore not needed).
- chirp_3 runs in the `us` and `eu` multi-regions at `{location}-speech.googleapis.com`. en-KE is
  not in v2's language table; en-GB, en-US and en-IN are.
- Synchronous recognize takes up to 1 minute and 10 MB of audio. The client sets no default timeout
  or retry for recognize, so an explicit timeout is passed.
- Constructing the client without credentials raises, so it is created on first use.
"""

import logging
from collections.abc import Callable, Sequence

from google.api_core import exceptions as google_exceptions
from google.api_core.client_options import ClientOptions
from google.auth import exceptions as auth_exceptions
from google.cloud.speech_v2 import SpeechAsyncClient
from google.cloud.speech_v2.types import cloud_speech
from google.oauth2 import service_account

from app.config import Settings
from app.speech.base import SpeechToTextError

log = logging.getLogger(__name__)

MAX_INLINE_BYTES = 10_000_000
PHRASE_BOOST = 10.0


def endpoint_for(location: str) -> str | None:
    """Regional endpoint for a location; None means the library's global default."""
    return None if location in ("", "global") else f"{location}-speech.googleapis.com"


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class GoogleSpeechToText:
    def __init__(
        self,
        *,
        project: str,
        location: str = "eu",
        model: str = "chirp_3",
        language_codes: Sequence[str] = ("en-GB",),
        timeout_seconds: float = 8.0,
        phrase_hints: Sequence[str] = (),
        credentials_file: str = "",
        client_factory: Callable[[], SpeechAsyncClient] | None = None,
    ) -> None:
        if not project:
            raise ValueError("GOOGLE_CLOUD_PROJECT must be set")
        if not language_codes:
            raise ValueError("GOOGLE_STT_LANGUAGE_CODES must list at least one language")
        self._recognizer = f"projects/{project}/locations/{location}/recognizers/_"
        self._location = location
        self._timeout_seconds = timeout_seconds
        self._credentials_file = credentials_file
        self._client_factory = client_factory or self._default_client
        self._client: SpeechAsyncClient | None = None
        self._config = self._build_config(model, language_codes, phrase_hints)

    @staticmethod
    def _build_config(
        model: str, language_codes: Sequence[str], phrase_hints: Sequence[str]
    ) -> cloud_speech.RecognitionConfig:
        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            model=model,
            language_codes=list(language_codes),
            features=cloud_speech.RecognitionFeatures(enable_automatic_punctuation=True),
        )
        if phrase_hints:
            phrases = [
                cloud_speech.PhraseSet.Phrase(value=p, boost=PHRASE_BOOST) for p in phrase_hints
            ]
            config.adaptation = cloud_speech.SpeechAdaptation(
                phrase_sets=[
                    cloud_speech.SpeechAdaptation.AdaptationPhraseSet(
                        inline_phrase_set=cloud_speech.PhraseSet(phrases=phrases)
                    )
                ]
            )
        return config

    def _default_client(self) -> SpeechAsyncClient:
        credentials = (
            service_account.Credentials.from_service_account_file(self._credentials_file)
            if self._credentials_file
            else None  # Application Default Credentials
        )
        endpoint = endpoint_for(self._location)
        options = ClientOptions(api_endpoint=endpoint) if endpoint else None
        return SpeechAsyncClient(credentials=credentials, client_options=options)

    async def transcribe(self, audio: bytes, *, content_type: str) -> str:
        if not audio:
            return ""
        if len(audio) > MAX_INLINE_BYTES:
            raise SpeechToTextError(f"audio is {len(audio)} bytes; inline limit is 10 MB")
        request = cloud_speech.RecognizeRequest(
            recognizer=self._recognizer, config=self._config, content=audio
        )
        try:
            if self._client is None:
                self._client = self._client_factory()
            response = await self._client.recognize(request=request, timeout=self._timeout_seconds)
        except (auth_exceptions.GoogleAuthError, OSError, ValueError) as exc:
            raise SpeechToTextError(f"Google credentials unavailable: {exc}") from exc
        except (google_exceptions.GoogleAPICallError, google_exceptions.RetryError) as exc:
            raise SpeechToTextError(f"Google Speech-to-Text {type(exc).__name__}: {exc}") from exc

        transcripts = (
            result.alternatives[0].transcript.strip()
            for result in response.results
            if result.alternatives
        )
        return " ".join(t for t in transcripts if t)


def build_google_speech_to_text(
    settings: Settings, *, client_factory: Callable[[], SpeechAsyncClient] | None = None
) -> GoogleSpeechToText:
    return GoogleSpeechToText(
        project=settings.google_cloud_project,
        location=settings.google_stt_location,
        model=settings.google_stt_model,
        language_codes=_split(settings.google_stt_language_codes),
        timeout_seconds=settings.google_stt_timeout_seconds,
        phrase_hints=_split(settings.google_stt_phrase_hints),
        credentials_file=settings.google_application_credentials,
        client_factory=client_factory,
    )
