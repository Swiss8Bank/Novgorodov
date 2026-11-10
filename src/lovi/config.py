from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    bot_token: SecretStr
    redis_url: str = "redis://localhost:6379/0"
    telegram_proxy: SecretStr | None = None
    media_proxy: SecretStr | None = None
    cookies_file: Path | None = None
    js_runtime: str = "node"
    telegram_api_base: str = ""
    welcome_animation_path: Path = Path("assets/welcome.gif")
    welcome_animation_file_id: str | None = None
    max_upload_mb: int = Field(49, ge=1, le=1900)
    max_duration_seconds: int = Field(1800, ge=1, le=14400)
    max_queue_size: int = Field(100, ge=1, le=10000)
    worker_concurrency: int = Field(2, ge=1, le=32)
    download_fragments: int = Field(4, ge=1, le=16)
    job_timeout_seconds: int = Field(600, ge=60, le=3600)
    probe_timeout_seconds: int = Field(60, ge=5, le=180)
    media_socket_timeout_seconds: int = Field(20, ge=5, le=120)
    max_jobs_per_user_hour: int = Field(20, ge=1, le=1000)
    link_cooldown_seconds: int = Field(4, ge=1, le=60)
    session_ttl_seconds: int = Field(1800, ge=60)
    cache_ttl_seconds: int = Field(604800, ge=60)
    data_dir: Path = Path("data")
    bot_mode: Literal["polling", "webhook"] = "polling"
    webhook_base_url: str = ""
    webhook_secret: SecretStr | None = None
    webhook_host: str = "0.0.0.0"
    webhook_port: int = Field(8080, ge=1, le=65535)

    @model_validator(mode="before")
    @classmethod
    def empty_optional(cls, values: dict) -> dict:
        for name in (
            "telegram_proxy",
            "media_proxy",
            "cookies_file",
            "webhook_secret",
            "welcome_animation_file_id",
        ):
            if values.get(name) == "":
                values[name] = None
        return values

    @model_validator(mode="after")
    def validate_runtime(self) -> "Settings":
        if self.max_upload_mb > 49 and not self.telegram_api_base:
            raise ValueError("MAX_UPLOAD_MB > 49 требует собственного TELEGRAM_API_BASE")
        if self.telegram_api_base:
            parsed = urlsplit(self.telegram_api_base)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("TELEGRAM_API_BASE должен быть URL собственного Bot API")
        if self.bot_mode == "webhook":
            secret = self.webhook_secret.get_secret_value() if self.webhook_secret else ""
            if not self.webhook_base_url.startswith("https://") or len(secret) < 32:
                raise ValueError("Webhook требует HTTPS WEBHOOK_BASE_URL и WEBHOOK_SECRET (32+)")
            if any(
                c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
                for c in secret
            ):
                raise ValueError("WEBHOOK_SECRET: допустимы только A-Z, a-z, 0-9, _ и -")
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1_000_000

    @property
    def telegram_timeout_seconds(self) -> int:
        return min(600, max(120, self.job_timeout_seconds // 3))

    @property
    def namespace(self) -> str:
        # Telegram file_id is only reusable by the same bot.
        import hashlib

        digest = hashlib.sha256(self.bot_token.get_secret_value().encode()).hexdigest()[:16]
        return f"lovi:{digest}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
