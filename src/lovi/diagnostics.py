"""Check extraction with the worker's configuration without Redis or Telegram calls."""

import asyncio
import json
import os
import tempfile
from importlib.util import find_spec
from pathlib import Path

from yt_dlp.version import __version__

from lovi.config import Settings
from lovi.downloader import Downloader
from lovi.extractor import MediaError
from lovi.models import MediaInfo, Selection
from lovi.urls import InvalidLink, normalize_url


async def diagnose(
    settings: Settings,
    url: str,
    *,
    check_cdn: bool = False,
    check_download: bool = False,
    height: int = 720,
) -> dict:
    report = {
        "yt_dlp_version": __version__,
        "curl_cffi_installed": find_spec("curl_cffi") is not None,
        "media_proxy_configured": bool(settings.media_proxy),
        "cookies_configured": bool(settings.cookies_file),
        "max_duration_seconds": settings.max_duration_seconds,
        "media_socket_timeout_seconds": settings.media_socket_timeout_seconds,
        "check": (
            "full_download"
            if check_download
            else "metadata_and_cdn_sample"
            if check_cdn
            else "metadata"
        ),
    }
    try:
        url = normalize_url(url)
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=settings.data_dir, prefix="probe-") as work:
            downloader = Downloader(settings)
            if check_download:
                selection = Selection.from_key(f"v{height}")
                artifact = await downloader.download(url, selection, Path(work))
                report.update(
                    ok=True,
                    stage="complete",
                    duration=artifact.duration,
                    width=artifact.width,
                    height=artifact.height,
                    download_bytes=await asyncio.to_thread(os.path.getsize, artifact.path),
                )
                return report
            elif check_cdn:
                selection = Selection.from_key(f"v{height}")
                data = await downloader.run(url, Path(work), selection, cdn_probe=True)
                info = MediaInfo.model_validate(data["info"])
                report["cdn_checks"] = data["cdn_checks"]
            else:
                info = await downloader.probe(url, Path(work))
        report.update(
            ok=True,
            platform=info.platform,
            duration=info.duration,
            duration_known=info.duration > 0,
            heights=info.heights,
        )
    except InvalidLink:
        report.update(ok=False, code="invalid_link", reason="unsupported_url")
    except MediaError as exc:
        report.update(
            ok=False, code=exc.code, reason=exc.reason, stage=exc.stage, cdn_host=exc.media_host
        )
    except Exception:
        # Never dump paths, settings, upstream error strings or proxy credentials.
        report.update(ok=False, code="internal", reason="diagnostic_failed")
    return report


def print_report(report: dict) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2))
