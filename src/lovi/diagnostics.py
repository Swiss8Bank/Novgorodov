"""Check extraction with the worker's configuration without Redis or Telegram calls."""

import json
import tempfile
from importlib.util import find_spec
from pathlib import Path

from yt_dlp.version import __version__

from lovi.config import Settings
from lovi.downloader import Downloader
from lovi.extractor import MediaError
from lovi.urls import InvalidLink, normalize_url


async def diagnose(settings: Settings, url: str) -> dict:
    report = {
        "yt_dlp_version": __version__,
        "curl_cffi_installed": find_spec("curl_cffi") is not None,
        "media_proxy_configured": bool(settings.media_proxy),
        "cookies_configured": bool(settings.cookies_file),
        "max_duration_seconds": settings.max_duration_seconds,
    }
    try:
        url = normalize_url(url)
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=settings.data_dir, prefix="probe-") as work:
            info = await Downloader(settings).probe(url, Path(work))
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
        report.update(ok=False, code=exc.code, reason=exc.reason)
    except Exception:
        # Never dump paths, settings, upstream error strings or proxy credentials.
        report.update(ok=False, code="internal", reason="diagnostic_failed")
    return report


def print_report(report: dict) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2))
