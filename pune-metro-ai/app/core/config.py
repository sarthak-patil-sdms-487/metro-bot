"""Application configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from environment variables and an optional .env file."""

    DATABASE_URL: str = (
        "postgresql+psycopg://pune_metro:pune_metro@postgres:5432/pune_metro"
    )
    WHATSAPP_VERIFY_TOKEN: str
    WHATSAPP_ACCESS_TOKEN: str
    WHATSAPP_PHONE_NUMBER_ID: str
    PRIMARY_LLM_API_KEY: str
    PRIMARY_LLM_MODEL: str = "openai/gpt-4o-mini"
    PRIMARY_LLM_BASE_URL: str = "https://openrouter.ai/api/v1"
    FALLBACK_LLM_API_KEY: str
    FALLBACK_LLM_MODEL: str = "gemini-3.1-flash-lite"
    JWT_SECRET_KEY: str
    JWT_EXPIRE_MINUTES: int = 30
    ADMIN_DASHBOARD_ORIGIN: str = "http://localhost:5173"
    QA_CACHE_TTL_HOURS: int = 24
    QA_CACHE_FUZZY_THRESHOLD: float = 0.92
    # OpenRouter gpt-4o-mini public rates converted at a configurable USD/INR rate.
    LLM_INPUT_USD_PER_MILLION: float = 0.15
    LLM_OUTPUT_USD_PER_MILLION: float = 0.60
    USD_TO_INR: float = 84.0
    SARVAM_STT_INR_PER_HOUR: float = 30.0
    SARVAM_TTS_INR_PER_10K_CHARS: float = 30.0
    MAX_REPLY_LENGTH: int = 800

    # WhatsApp Calling is optional. When disabled or unconfigured the chat webhook
    # continues to operate in the same process.
    WHATSAPP_CALLING_ENABLED: bool = False
    WHATSAPP_CALLING_ACCESS_TOKEN: str = ""
    WHATSAPP_CALLING_PHONE_NUMBER_ID: str = ""
    WHATSAPP_CALLING_APP_SECRET: str = ""
    WHATSAPP_CALLING_API_VERSION: str = "v23.0"

    # Streaming speech providers used only for calls.
    SARVAM_API_KEY: str = ""
    SARVAM_STT_MODEL: str = "saaras:v3"
    SARVAM_STT_MODE: str = "transcribe"
    SARVAM_TTS_MODEL: str = "bulbul:v3"
    SARVAM_TTS_SPEAKER_ENGLISH: str = "shreya"
    SARVAM_TTS_SPEAKER_HINDI: str = "shreya"
    SARVAM_TTS_SPEAKER_MARATHI: str = "shreya"
    SARVAM_TTS_PACE: float = 1.0
    SARVAM_TTS_TEMPERATURE: float = 0.6
    CALL_RECORDINGS_DIR: str = "/app/recordings"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
