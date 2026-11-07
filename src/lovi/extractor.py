"""Isolated yt-dlp entry point. No tokens, URLs or upstream errors go to logs."""

import json
import math
import resource
import subprocess
import sys
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from lovi.models import Artifact, MediaInfo, Selection
from lovi.urls import normalize_url

HEIGHTS = (240, 360, 480, 720, 1080, 1440, 2160)


def resolution_of(fmt: dict) -> int:
    dimensions = [fmt.get(key) for key in ("width", "height") if fmt.get(key)]
    return min(dimensions, default=0)


class MediaError(Exception):
    def __init__(self, code: str, reason: str = "unspecified"):
        super().__init__(code)
        self.code = code
        # Only fixed labels cross the subprocess boundary or enter logs.
        self.reason = reason if reason in REASONS else "unspecified"


REASONS = {
    "unspecified",
    "login_required",
    "rate_limit_or_login",
    "http_429",
    "http_403",
    "proxy_407",
    "network_timeout",
    "connection_failed",
    "tls_error",
    "empty_media_response",
    "extractor_error",
    "missing_impersonation",
    "duration_limit",
    "file_duration_missing",
}


def classify_error(exc: Exception) -> MediaError:
    raw = str(exc).lower()
    if any(word in raw for word in ("file is larger", "max-filesize", "too large")):
        return MediaError("too_large")
    if "requested format" in raw or "no video in this post" in raw:
        return MediaError("format")
    if "407" in raw or "proxy authentication" in raw:
        return MediaError("proxy_auth", "proxy_407")
    if "certificate verify failed" in raw or "certificate_verify_failed" in raw:
        return MediaError("network", "tls_error")
    if "timed out" in raw or "timeout" in raw:
        return MediaError("timeout", "network_timeout")
    if "429" in raw:
        return MediaError("rate_limit", "http_429")
    rate = any(word in raw for word in ("rate-limit", "rate limit", "too many requests"))
    login = any(
        word in raw
        for word in (
            "login required",
            "log in",
            "logged-in",
            "login page",
            "sign in",
            "registered users",
        )
    )
    if rate and login:
        return MediaError("site_access", "rate_limit_or_login")
    if rate:
        return MediaError("rate_limit", "http_429")
    # An empty response with a generic login hint does not prove cookies are required.
    if "empty media response" in raw:
        return MediaError("site_access", "empty_media_response")
    if login:
        return MediaError("login", "login_required")
    if "403" in raw:
        return MediaError("site_access", "http_403")
    if "impersonat" in raw and any(word in raw for word in ("not available", "not found")):
        return MediaError("internal", "missing_impersonation")
    if any(
        word in raw
        for word in (
            "connection refused",
            "unable to connect",
            "name resolution",
            "proxy connection",
        )
    ):
        return MediaError("network", "connection_failed")
    return MediaError("unavailable", "extractor_error")


class QuietLogger:
    def debug(self, *_):
        pass

    def info(self, *_):
        pass

    def warning(self, *_):
        pass

    def error(self, *_):
        pass


def known_duration(value) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def check_info(info: dict, max_duration: int) -> None:
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
        raise MediaError("live")
    if info.get("_type") in {"playlist", "multi_video"} or "entries" in info:
        raise MediaError("unavailable")
    # Missing website metadata is common on Reels. Bound downloads by bytes/time,
    # then independently verify the file duration with ffprobe before delivery.
    duration = known_duration(info.get("duration"))
    if duration is not None and duration > max_duration:
        raise MediaError("duration", "duration_limit")
    extractor = str(info.get("extractor_key", "")).lower()
    if extractor not in {"youtube", "tiktok", "instagram"}:
        raise MediaError("unavailable")


def summarize(info: dict, url: str) -> MediaInfo:
    formats = info.get("formats") or [info]
    available = [
        f
        for f in formats
        if f.get("vcodec") != "none" and f.get("ext") == "mp4" and not f.get("has_drm")
    ]
    # Portrait videos: 1080x1920 belongs to the 1080p tier, not 2160p.
    resolutions = {resolution_of(f) for f in available}
    # Only advertise tiers with an actual source format, never invent downscaled variants.
    heights = sorted(
        {
            tier
            for res in resolutions
            if res > 0
            if (tier := next((h for h in HEIGHTS if res <= h), None)) is not None
        }
    )
    if available and not heights and 0 in resolutions:
        heights = [0]  # Unknown dimensions: offer original, never claim a resolution.
    key = str(info.get("extractor_key", "")).lower()
    return MediaInfo(
        url=url,
        title=info.get("title") or "Ролик",
        author=info.get("uploader") or "",
        platform={"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}[key],
        duration=math.ceil(known_duration(info.get("duration")) or 0),
        heights=heights,
        has_audio=any(
            f.get("acodec") != "none" and f.get("ext") in {"mp4", "m4a", "webm"} for f in formats
        ),
        has_thumbnail=bool(info.get("thumbnail") or info.get("thumbnails")),
    )


def video_format(height: int):
    def selector(context):
        # Context formats are sorted worst -> best by yt-dlp. Resolution takes priority,
        # then H.264 for Telegram compatibility, then yt-dlp's bitrate/codec ordering.
        candidates = []
        formats = context.get("formats", [])
        for index, fmt in enumerate(formats):
            resolution = resolution_of(fmt)
            if (
                fmt.get("ext") == "mp4"
                and fmt.get("vcodec") != "none"
                and not fmt.get("has_drm")
                and (height == 0 or 0 < resolution <= height)
            ):
                candidates.append(
                    (resolution, str(fmt.get("vcodec", "")).startswith(("avc", "h264")), index, fmt)
                )
        if not candidates:
            return
        video = max(candidates, key=lambda item: item[:3])[3]
        # Unknown acodec on a progressive MP4 (common on Reels) does not mean silence.
        if video.get("acodec") != "none":
            yield video
            return
        audio = next(
            (
                fmt
                for fmt in reversed(formats)
                if fmt.get("ext") == "m4a"
                and fmt.get("vcodec") == "none"
                and fmt.get("acodec") not in {None, "none"}
                and not fmt.get("has_drm")
            ),
            None,
        )
        if audio is None:
            # A genuinely silent source is supported; don't produce silent YouTube files
            # when an incompatible audio track exists.
            if any(f.get("acodec") not in {None, "none"} for f in formats):
                return
            yield video
            return
        yield {
            "format_id": f"{video['format_id']}+{audio['format_id']}",
            "ext": "mp4",
            "requested_formats": [video, audio],
            "protocol": f"{video['protocol']}+{audio['protocol']}",
            "width": video.get("width"),
            "height": video.get("height"),
        }

    return selector


def options(request: dict, directory: Path) -> dict:
    limit = int(request["max_bytes"])

    def progress(state: dict) -> None:
        if state.get("downloaded_bytes", 0) > limit * 2:
            raise MediaError("too_large")

    def match_filter(info, *, incomplete=False):
        if not incomplete:
            check_info(info, request["max_duration"])
        return None

    opts = {
        "quiet": True,
        "no_warnings": True,
        "logger": QuietLogger(),
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_retries": 2,
        "concurrent_fragment_downloads": request["fragments"],
        "outtmpl": str(directory / "media.%(ext)s"),
        "restrictfilenames": True,
        "max_filesize": limit * 2,
        "progress_hooks": [progress],
        "overwrites": True,
        "match_filter": match_filter,
        "js_runtimes": {request["js_runtime"]: {}},
        # Challenge scripts are supplied by the pinned yt-dlp-ejs dependency, not remote code.
        "remote_components": [],
    }
    if request.get("proxy"):
        opts["proxy"] = request["proxy"]
    if request.get("cookies_file"):
        opts["cookiefile"] = request["cookies_file"]
    return opts


def inspect_media_file(path: Path, max_duration: int) -> tuple[float, dict]:
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height,duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    data = json.loads(probe.stdout)
    streams = data.get("streams", [])
    durations = [known_duration(data.get("format", {}).get("duration"))]
    durations += [known_duration(stream.get("duration")) for stream in streams]
    actual = max((value for value in durations if value is not None), default=None)
    if actual is None:
        raise MediaError("duration_unknown", "file_duration_missing")
    if actual > max_duration:
        raise MediaError("duration", "duration_limit")
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    return actual, video


def extract(request: dict, directory: Path) -> dict:
    url = normalize_url(request["url"])
    opts = options(request, directory)
    if request["action"] == "probe":
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            raise MediaError("unavailable")
        check_info(info, request["max_duration"])
        return summarize(info, url).model_dump()

    selection = Selection.model_validate(request["selection"])
    if selection.kind == "video":
        opts.update(
            {
                "format": video_format(selection.height),
                "merge_output_format": "mp4",
                "postprocessor_args": {"merger+ffmpeg_o": ["-movflags", "+faststart"]},
            }
        )
    elif selection.kind == "audio":
        opts.update(
            {
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
        )
    else:
        opts.update(
            {
                "skip_download": True,
                "writethumbnail": True,
                "postprocessors": [{"key": "FFmpegThumbnailsConvertor", "format": "jpg"}],
            }
        )
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if not info:
        raise MediaError("unavailable")
    check_info(info, request["max_duration"])
    suffix = {"video": ".mp4", "audio": ".mp3", "cover": ".jpg"}[selection.kind]
    candidates = list(directory.glob(f"media*{suffix}"))
    if not candidates:
        raise MediaError("format")
    path = max(candidates, key=lambda p: p.stat().st_size)
    if not 0 < path.stat().st_size <= request["max_bytes"]:
        raise MediaError("too_large")
    width, height, codec = 0, 0, "h264"
    if selection.kind == "video":
        # Move the MP4 index to the front without re-encoding for immediate playback.
        ready = directory / "ready.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(ready),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        path = ready
    actual_duration = known_duration(info.get("duration")) or 0
    if selection.kind in {"video", "audio"}:
        actual_duration, stream = inspect_media_file(path, request["max_duration"])
        if selection.kind == "video":
            if not stream:
                raise MediaError("format")
            width, height, codec = stream["width"], stream["height"], stream["codec_name"]
    if path.stat().st_size > request["max_bytes"]:
        raise MediaError("too_large")
    return Artifact(
        path=str(path),
        kind=selection.kind,
        width=width,
        height=height,
        video_codec=codec,
        duration=math.ceil(actual_duration),
    ).model_dump()


def main() -> None:
    directory = Path(sys.argv[1]).resolve()
    request = json.loads((directory / "request.json").read_text())
    # Enforced by the OS for media, FFmpeg output, and diagnostic files.
    max_file = int(request["max_bytes"]) * 2 + 2_000_000
    resource.setrlimit(resource.RLIMIT_FSIZE, (max_file, max_file))
    try:
        result = {"ok": True, "data": extract(request, directory)}
    except MediaError as exc:
        result = {"ok": False, "code": exc.code, "reason": exc.reason}
    except DownloadError as exc:
        error = classify_error(exc)
        result = {"ok": False, "code": error.code, "reason": error.reason}
    except Exception:
        result = {"ok": False, "code": "internal"}
    (directory / "result.json").write_text(json.dumps(result))


if __name__ == "__main__":
    main()
