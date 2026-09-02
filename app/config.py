from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key: str = "dev-api-key-change-me"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    database_path: str = "./data/webhooks.db"
    webhook_default_secret: str = "dev-webhook-secret"
    log_level: str = "INFO"
    webhook_timeout_seconds: float = 5.0
    webhook_max_attempts: int = 5
    webhook_backoff_base_seconds: float = 1.0

    events_exchange: str = "events"
    events_queue: str = "notifications"
    events_dlq: str = "notifications.dlq"


@lru_cache
def get_settings() -> Settings:
    return Settings()
