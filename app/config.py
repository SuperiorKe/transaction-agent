from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Field names map 1:1 to `.env.example`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Negotiation agent LLM (Anthropic Messages API)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    # "" omits output_config.effort, for models that don't support it
    anthropic_effort: str = "low"
    anthropic_refusal_fallback: str = "default"  # "" disables server-side refusal fallbacks

    # Telephony (Twilio Voice).  The retired Africa's Talking settings remain below so an
    # existing local .env continues to load while the pivot is being completed.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_voice_number: str = ""  # the Twilio number calls are placed from, E.164

    # Telephony (Africa's Talking Voice; retired)
    at_username: str = ""
    at_api_key: str = ""
    at_voice_number: str = ""  # the AT voice number calls are placed from, E.164
    at_tts_voice: str = ""  # Google TTS voice name for <Say>; "" uses AT's default
    at_record_max_seconds: int = 15
    at_record_silence_timeout_seconds: int = 3
    at_recording_hosts: str = "africastalking.com,at-internal.com"

    # Speech-to-text (Google Cloud Speech-to-Text v2)
    google_application_credentials: str = ""  # service-account JSON path; "" uses ADC
    google_cloud_project: str = ""
    google_stt_location: str = "eu"  # chirp_3 runs in the "us" and "eu" multi-regions
    google_stt_model: str = "chirp_3"
    google_stt_language_codes: str = "en-GB"  # en-KE isn't supported by v2; comma-separated
    google_stt_timeout_seconds: float = 8.0
    google_stt_phrase_hints: str = ""  # comma-separated phrases to boost; "" disables
    voice_webhook_secret: str = ""  # path segment of the callback URL; empty disables the webhook
    webhook_base_url: str = ""  # public tunnel URL the provider calls back
    app_url: str = "http://localhost:8000"

    database_url: str = "sqlite:///./transaction_agent.db"
    timezone: str = "Africa/Nairobi"

    provider_1_name: str = ""
    provider_1_phone: str = ""
    provider_2_name: str = ""
    provider_2_phone: str = ""
    provider_location: str = "Nairobi"

    max_calls_per_day: int = 40
    call_hard_end_seconds: int = 180
    confirm_retry_delay_seconds: int = 120


@lru_cache
def get_settings() -> Settings:
    return Settings()
