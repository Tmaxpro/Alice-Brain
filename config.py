from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    ES_URL: str = "http://localhost:9200"
    ANTHROPIC_API_KEY: str
    ABUSEIPDB_KEY: Optional[str] = None
    REDIS_URL: Optional[str] = None
    SECRET_KEY: str = "super_secret_key_change_me"
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
