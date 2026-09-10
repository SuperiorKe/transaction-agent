from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Field names map 1:1 to `.env.example` (epic #10, contract 6)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    openai_realtime_model: str = "gpt-realtime-2.1-mini"
    openai_realtime_voice: str = "alloy"
    openai_transcribe_model: str = ""
    openai_text_model: str = ""

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    voice_from_number: str = ""
    webhook_base_url: str = ""
    app_url: str = "http://localhost:8000"

    database_url: str = "sqlite:///./transaction_agent.db"
    timezone: str = "Africa/Nairobi"

    provider_1_name: str = ""
    provider_1_phone: str = ""
    provider_2_name: str = ""
    provider_2_phone: str = ""
    provider_location: str = "Nairobi"

    max_calls_per_day: int = 40
    call_nudge_seconds: int = 40
    call_hard_end_seconds: int = 75
    call_time_limit_seconds: int = 90
    confirm_nudge_seconds: int = 25
    confirm_time_limit_seconds: int = 45
    ring_timeout_seconds: int = 25
    confirm_retry_delay_seconds: int = 120


@lru_cache
def get_settings() -> Settings:
    return Settings()
