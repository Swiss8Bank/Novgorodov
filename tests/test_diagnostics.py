import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from lovi.diagnostics import diagnose
from lovi.downloader import Downloader
from lovi.extractor import MediaError, classify_error
from lovi.jobs import inspect_media
from lovi.urls import normalize_url


@pytest.mark.parametrize(
    ("message", "code", "reason"),
    [
        ("HTTP Error 407: Proxy Authentication Required", "proxy_auth", "proxy_407"),
        ("Connection timed out", "timeout", "network_timeout"),
        ("HTTP Error 429: Too Many Requests", "rate_limit", "http_429"),
        ("HTTP Error 403: Forbidden", "site_access", "http_403"),
        ("certificate verify failed", "network", "tls_error"),
        ("Please log in to access this content", "login", "login_required"),
        (
            "Requested content is not available, rate-limit reached or login required",
            "site_access",
            "rate_limit_or_login",
        ),
        (
            "Instagram sent an empty media response. Check without being logged-in",
            "site_access",
            "empty_media_response",
        ),
        ("Requested format is not available", "format", "unspecified"),
        ("Unknown upstream failure", "unavailable", "extractor_error"),
    ],
)
def test_fixed_error_classification(message, code, reason):
    error = classify_error(DownloadError(message))
    assert (error.code, error.reason) == (code, reason)


def test_errors_never_preserve_secrets():
    error = classify_error(DownloadError("HTTP Error 403 via http://login:secret@host/?token=abc"))
    assert "secret" not in str(error)
    assert "login" not in error.reason
    assert MediaError("unavailable", "password=secret\ninjected").reason == "unspecified"


def test_reported_reel_url_normalization():
    assert normalize_url("https://www.instagram.com/reel/DctUTDnNSWx/?vrfl=tracking") == (
        "https://www.instagram.com/reel/DctUTDnNSWx/"
    )


def test_instagram_impersonation_backend_available():
    with YoutubeDL({"quiet": True}) as ydl:
        assert "CurlCFFI" in ydl._request_director.handlers
        assert ydl._get_available_impersonate_targets()


async def test_diagnosis_uses_worker_config_and_doesnt_print_secrets(settings, monkeypatch):
    monkeypatch.setattr(
        Downloader,
        "probe",
        AsyncMock(side_effect=MediaError("site_access", "empty_media_response")),
    )
    report = await diagnose(settings, "https://www.instagram.com/reel/DctUTDnNSWx/")
    assert report["ok"] is False
    assert report["reason"] == "empty_media_response"
    assert report["curl_cffi_installed"] is True
    assert "bot_token" not in json.dumps(report)
    assert not list(settings.data_dir.iterdir())


async def test_successful_diagnosis(settings, monkeypatch, info):
    monkeypatch.setattr(Downloader, "probe", AsyncMock(return_value=info))
    report = await diagnose(settings, info.url)
    assert report["ok"] is True
    assert report["heights"] == info.heights
    assert "url" not in report
    assert "title" not in report


async def test_worker_logs_fixed_reason(store, settings, fake_bot, caplog):
    ctx = {
        "store": store,
        "settings": settings,
        "bot": fake_bot,
        "downloader": SimpleNamespace(
            probe=AsyncMock(side_effect=MediaError("site_access", "empty_media_response"))
        ),
    }
    await inspect_media(ctx, 1, 1, 10, "https://www.instagram.com/reel/DctUTDnNSWx/", "diag-job")
    assert "reason=empty_media_response" in caplog.text
    assert "instagram.com" not in caplog.text
    assert "пустой ответ" in fake_bot.edit_message_text.call_args.kwargs["text"]
