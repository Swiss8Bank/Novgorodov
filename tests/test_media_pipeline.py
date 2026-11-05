import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from yt_dlp import YoutubeDL

from lovi import extractor


@pytest.fixture
def local_media(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe required for actual media pipeline tests")
    source = tmp_path / "source.mp4"
    cover = tmp_path / "cover.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:r=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-update",
            "1",
            str(cover),
        ],
        check=True,
        capture_output=True,
    )
    metadata = {
        "id": "fixture",
        "title": "Fixture",
        "extractor_key": "Youtube",
        "extractor": "youtube",
        "duration": 1,
        "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "thumbnails": [{"url": cover.as_uri(), "id": "cover", "ext": "jpg"}],
        "formats": [
            {
                "format_id": "test",
                "url": source.as_uri(),
                "ext": "mp4",
                "width": 320,
                "height": 240,
                "vcodec": "avc1",
                "acodec": "aac",
                "protocol": "file",
            }
        ],
    }

    class LocalYDL(YoutubeDL):
        def __init__(self, params):
            super().__init__({**params, "enable_file_urls": True})

        def extract_info(self, url, download=True, **kwargs):
            # Only replace website extraction. Real yt-dlp downloading/postprocessors follow.
            return self.process_ie_result(copy.deepcopy(metadata), download=download)

    monkeypatch.setattr(extractor, "YoutubeDL", LocalYDL)
    work = tmp_path / "job"
    work.mkdir()
    return work


@pytest.mark.parametrize(
    ("kind", "ext", "codec"),
    [
        ("video", ".mp4", "h264"),
        ("audio", ".mp3", "mp3"),
        ("cover", ".jpg", "mjpeg"),
    ],
)
def test_actual_ytdlp_ffmpeg_pipeline(local_media, kind, ext, codec):
    result = extractor.extract(
        {
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "action": "download",
            "selection": {"kind": kind, "height": 240 if kind == "video" else 0},
            "max_bytes": 10_000_000,
            "max_duration": 60,
            "fragments": 2,
            "js_runtime": "node",
        },
        local_media,
    )
    path = Path(result["path"])
    assert path.suffix == ext
    assert path.stat().st_size > 0
    raw = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    codecs = {stream["codec_name"] for stream in json.loads(raw.stdout)["streams"]}
    assert codec in codecs
    if kind == "video":
        assert "aac" in codecs
        data = path.read_bytes()
        assert data.index(b"moov") < data.index(b"mdat")
        assert (result["width"], result["height"]) == (320, 240)
