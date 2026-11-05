import pytest
from pydantic import ValidationError

from lovi.config import Settings


def test_empty_optional_fields():
    s = Settings(
        bot_token="dummy",
        telegram_proxy="",
        media_proxy="",
        cookies_file="",
        webhook_secret="",
        _env_file=None,
    )
    assert s.telegram_proxy is s.media_proxy is s.cookies_file is s.webhook_secret is None


def test_cloud_upload_limit():
    with pytest.raises(ValidationError, match="TELEGRAM_API_BASE"):
        Settings(bot_token="secret-token", max_upload_mb=100, _env_file=None)
    s = Settings(
        bot_token="dummy",
        max_upload_mb=1900,
        telegram_api_base="http://local-api:8081",
        _env_file=None,
    )
    assert s.max_upload_bytes == 1_900_000_000


def test_validation_doesnt_print_token():
    with pytest.raises(ValidationError) as caught:
        Settings(bot_token="very-sensitive-token", max_upload_mb=100, _env_file=None)
    assert "very-sensitive-token" not in str(caught.value)


@pytest.mark.parametrize(
    ("url", "secret"),
    [
        ("http://example.com", "s" * 32),
        ("https://example.com", "short"),
        ("https://example.com", "not-valid!" * 5),
    ],
)
def test_webhook_config_validation(url, secret):
    with pytest.raises(ValidationError):
        Settings(
            bot_token="dummy",
            bot_mode="webhook",
            webhook_base_url=url,
            webhook_secret=secret,
            _env_file=None,
        )
