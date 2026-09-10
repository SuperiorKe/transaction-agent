from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Field names map 1:1 to `.env.example`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Negotiation agent LLM (Anthropic Messages API)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    anthropic_effort: str = (
        "low"  # "" omits output_config.effort (required for models without effort)
    )
    anthropic_refusal_fallback: str = "default"  # "" disables server-side refusal fallbacks

    # Telephony (Africa's Talking Voice)
    at_username: str = ""
    at_api_key: str = ""
    at_voice_number: str = ""  # the AT voice number calls are placed from, E.164
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
