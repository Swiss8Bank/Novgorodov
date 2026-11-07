import asyncio
import json
import sys
from pathlib import Path

import pytest

from lovi import downloader as module
from lovi.downloader import Downloader, validate_artifact
from lovi.extractor import MediaError
from lovi.models import Artifact


def is_process_stopped(pid: int) -> bool:
    status = Path(f"/proc/{pid}/stat")
    return not status.exists() or status.read_text().split()[2] == "Z"


def test_reject_path_escape(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")
    with pytest.raises(MediaError, match="internal"):
        validate_artifact(Artifact(path=str(outside), kind="video"), work, 100)


def test_reject_oversized_artifact(tmp_path):
    path = tmp_path / "file.mp4"
    path.write_bytes(b"12345")
    with pytest.raises(MediaError, match="too_large"):
        validate_artifact(Artifact(path=str(path), kind="video"), tmp_path, 4)


async def test_subprocess_result_and_cookie_copy(settings, tmp_path, monkeypatch):
    cookies = tmp_path / "source-cookies.txt"
    cookies.write_text("original")
    settings.cookies_file = cookies
    work = tmp_path / "work"
    work.mkdir()
    create = asyncio.create_subprocess_exec

    async def local_process(*args, **kwargs):
        script = (
            "import json,sys;from pathlib import Path; p=Path(sys.argv[1]);"
            "r=json.loads((p/'request.json').read_text());"
            "Path(r['cookies_file']).write_text('updated');"
            "(p/'result.json').write_text(json.dumps({'ok':True,'data':{'url':r['url'],"
            "'platform':'YouTube','title':'local'}}))"
        )
        return await create(sys.executable, "-c", script, args[-1], **kwargs)

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", local_process)
    result = await Downloader(settings).probe("https://youtu.be/dQw4w9WgXcQ", work)
    assert result.title == "local"
    assert cookies.read_text() == "original"
    assert (work / "cookies.txt").read_text() == "updated"
    assert (work / "request.json").stat().st_mode & 0o777 == 0o600


async def test_timeout_kills_subprocess(settings, tmp_path, monkeypatch):
    settings.probe_timeout_seconds = 0.15
    create = asyncio.create_subprocess_exec
    processes = []

    async def slow_process(*args, **kwargs):
        process = await create(sys.executable, "-c", "import time;time.sleep(60)", **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", slow_process)
    with pytest.raises(MediaError, match="timeout"):
        await Downloader(settings).probe("https://youtu.be/dQw4w9WgXcQ", tmp_path)
    assert processes[0].returncode is not None


async def test_cancel_kills_ffmpeg_group(settings, tmp_path, monkeypatch):
    create = asyncio.create_subprocess_exec
    processes = []
    ready = asyncio.Event()

    async def slow_process(*args, **kwargs):
        script = (
            "import subprocess,sys,time; from pathlib import Path; "
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
            "Path(sys.argv[1]).write_text(str(p.pid));time.sleep(60)"
        )
        process = await create(sys.executable, "-c", script, str(tmp_path / "child.pid"), **kwargs)
        processes.append(process)
        ready.set()
        return process

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", slow_process)
    task = asyncio.create_task(Downloader(settings).probe("https://youtu.be/dQw4w9WgXcQ", tmp_path))
    await ready.wait()
    for _ in range(100):
        if (tmp_path / "child.pid").exists():
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None
    pid = int((tmp_path / "child.pid").read_text())
    # Linux can retain a reaped child's zombie briefly, but no runnable FFmpeg may survive.
    assert await asyncio.to_thread(is_process_stopped, pid)


async def test_upstream_error_code_survives(settings, tmp_path, monkeypatch):
    create = asyncio.create_subprocess_exec

    async def failed_process(*args, **kwargs):
        script = "import sys;from pathlib import Path;Path(sys.argv[1]).write_text(sys.argv[2])"
        return await create(
            sys.executable,
            "-c",
            script,
            str(tmp_path / "result.json"),
            json.dumps(
                {
                    "ok": False,
                    "code": "unavailable",
                    "reason": "empty_media_response",
                    "stage": "media",
                    "media_host": "video.example.com",
                }
            ),
            **kwargs,
        )

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", failed_process)
    with pytest.raises(MediaError, match="unavailable") as caught:
        await Downloader(settings).probe("https://youtu.be/dQw4w9WgXcQ", tmp_path)
    assert caught.value.reason == "empty_media_response"
    assert caught.value.stage == "media"
    assert caught.value.media_host == "video.example.com"
