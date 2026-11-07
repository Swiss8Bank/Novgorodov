import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.networking._curlcffi import CurlCFFIRH
from yt_dlp.networking.common import Response
from yt_dlp.networking.impersonate import ImpersonateTarget

from lovi.diagnostics import diagnose
from lovi.downloader import Downloader
from lovi.extractor import MediaError, options, probe_media_connections
from lovi.models import Artifact, Selection


def request_options(url="https://www.instagram.com/reel/DctUTDnNSWx/"):
    return {
        "url": url,
        "max_bytes": 10_000_000,
        "max_duration": 60,
        "fragments": 4,
        "js_runtime": "node",
        "socket_timeout": 35,
    }


def media_info():
    return {
        "http_headers": {"User-Agent": "parent-UA"},
        "formats": [
            {
                "url": "https://video.cdn.example/file.mp4?token=SECRET",
                "format_id": "video",
                "width": 1280,
                "height": 720,
                "vcodec": "avc1",
                "acodec": "aac",
                "ext": "mp4",
                "protocol": "https",
                "http_headers": {"User-Agent": "stream-UA"},
            }
        ],
    }


def test_instagram_cdn_uses_browser_backend_and_bounded_range(tmp_path, monkeypatch):
    calls = []
    streams = []

    def send(self, request):
        calls.append(request)
        fp = io.BytesIO(b"v" * 8192)
        streams.append(fp)
        return Response(fp, request.url, {"Content-Type": "video/mp4"}, 206)

    monkeypatch.setattr(CurlCFFIRH, "_send", send)
    opts = options(request_options(), tmp_path)
    assert isinstance(opts["impersonate"], ImpersonateTarget)
    assert opts["socket_timeout"] == 35
    with YoutubeDL(opts) as ydl:
        checks = probe_media_connections(ydl, media_info(), Selection(kind="video", height=720))
    assert len(calls) == 1
    assert calls[0].headers["Range"] == "bytes=0-1023"
    assert calls[0].headers["User-Agent"] == "stream-UA"
    assert checks[0]["sample_bytes"] == 1024
    assert checks[0]["host"] == "video.cdn.example"
    assert checks[0]["http_status"] == 206
    assert all(stream.closed for stream in streams)
    assert "SECRET" not in json.dumps(checks)


def test_other_sites_do_not_force_instagram_profile(tmp_path):
    assert "impersonate" not in options(
        request_options("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), tmp_path
    )


def test_cdn_timeout_has_stage_and_host_without_signed_url():
    def fail(request):
        raise TimeoutError()

    ydl = SimpleNamespace(urlopen=fail)
    with pytest.raises(MediaError) as caught:
        probe_media_connections(ydl, media_info(), Selection(kind="video", height=720))
    assert caught.value.code == "timeout"
    assert caught.value.reason == "network_timeout"
    assert caught.value.stage == "media"
    assert caught.value.media_host == "video.cdn.example"
    assert "SECRET" not in str(caught.value)


def test_cdn_html_challenge_is_not_reported_as_success():
    response = Response(
        io.BytesIO(b"<html>login</html>"),
        "https://video.cdn.example/file",
        {"Content-Type": "text/html"},
    )
    with pytest.raises(MediaError) as caught:
        probe_media_connections(
            SimpleNamespace(urlopen=lambda request: response),
            media_info(),
            Selection(kind="video", height=720),
        )
    assert caught.value.code == "site_access"
    assert caught.value.reason == "media_not_video"
    assert response.fp.closed


async def test_cdn_diagnostic_uses_one_extraction_and_worker_settings(settings, monkeypatch, info):
    run = AsyncMock(
        return_value={"info": info.model_dump(), "cdn_checks": [{"sample_bytes": 1024}]}
    )
    probe = AsyncMock()
    monkeypatch.setattr(Downloader, "run", run)
    monkeypatch.setattr(Downloader, "probe", probe)
    report = await diagnose(settings, info.url, check_cdn=True, height=720)
    assert report["ok"] is True
    assert report["check"] == "metadata_and_cdn_sample"
    assert report["cdn_checks"][0]["sample_bytes"] == 1024
    assert run.call_args.kwargs["cdn_probe"] is True
    assert run.call_args.args[2].height == 720
    probe.assert_not_awaited()
    assert not list(settings.data_dir.iterdir())


async def test_failed_cdn_report_does_not_claim_metadata_success(settings, monkeypatch, info):
    monkeypatch.setattr(
        Downloader,
        "run",
        AsyncMock(
            side_effect=MediaError("timeout", "network_timeout", "media", "video.cdn.example")
        ),
    )
    report = await diagnose(settings, info.url, check_cdn=True)
    assert report["ok"] is False
    assert report["stage"] == "media"
    assert report["cdn_host"] == "video.cdn.example"


def test_unknown_stage_and_host_cannot_inject_logs():
    error = MediaError("timeout", "network_timeout", "arbitrary\ntext", "password=secret\nlog")
    assert error.stage == "unspecified"
    assert error.media_host is None


async def test_full_download_diagnostic_checks_file_and_cleans_up(settings, monkeypatch, info):
    async def download(self, url, selection, directory):
        path = directory / "ready.mp4"
        path.write_bytes(b"video-fixture")
        return Artifact(path=str(path), kind="video", duration=3, width=1280, height=720)

    monkeypatch.setattr(Downloader, "download", download)
    report = await diagnose(settings, info.url, check_download=True)
    assert report["ok"] is True
    assert report["check"] == "full_download"
    assert report["duration"] == 3
    assert report["download_bytes"] == len(b"video-fixture")
    assert "path" not in report
    assert not list(settings.data_dir.iterdir())


async def test_full_download_failure_retains_stage(settings, monkeypatch, info):
    monkeypatch.setattr(
        Downloader,
        "download",
        AsyncMock(side_effect=MediaError("timeout", "network_timeout", "media")),
    )
    report = await diagnose(settings, info.url, check_download=True)
    assert report["ok"] is False
    assert report["stage"] == "media"
    assert report["reason"] == "network_timeout"
