import asyncio
import json
import os
import shutil
import signal
import sys
from pathlib import Path

from lovi.config import Settings
from lovi.extractor import MediaError
from lovi.models import Artifact, MediaInfo, Selection


def directory_bytes(directory: Path) -> int:
    total = 0
    for path in directory.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            # FFmpeg/yt-dlp may replace a fragment while it is being counted.
            pass
    return total


def validate_artifact(artifact: Artifact, directory: Path, limit: int) -> None:
    path = Path(artifact.path).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise MediaError("internal")
    if not 0 < path.stat().st_size <= limit:
        raise MediaError("too_large")


class Downloader:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(self, url: str, directory: Path, selection: Selection | None = None) -> dict:
        s = self.settings
        if shutil.disk_usage(directory).free < s.max_upload_bytes * 3 + 100_000_000:
            raise MediaError("disk")
        request = {
            "url": url,
            "action": "download" if selection else "probe",
            "selection": selection.model_dump() if selection else None,
            "max_bytes": s.max_upload_bytes,
            "max_duration": s.max_duration_seconds,
            "fragments": s.download_fragments,
            "js_runtime": s.js_runtime,
            "proxy": s.media_proxy.get_secret_value() if s.media_proxy else None,
            "cookies_file": None,
        }
        # yt-dlp saves cookies on exit. Work on a private per-job copy, never mutate secrets.
        if s.cookies_file:
            cookie_copy = directory / "cookies.txt"
            await asyncio.to_thread(shutil.copyfile, s.cookies_file, cookie_copy)
            cookie_copy.chmod(0o600)
            request["cookies_file"] = str(cookie_copy)
        request_path = directory / "request.json"
        request_path.write_text(json.dumps(request))
        request_path.chmod(0o600)
        timeout = (
            max(1, s.job_timeout_seconds - s.telegram_timeout_seconds - 35)
            if selection
            else s.probe_timeout_seconds
        )
        with (directory / "process.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "lovi.extractor",
                str(directory),
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            try:
                async with asyncio.timeout(timeout):
                    while process.returncode is None:
                        try:
                            await asyncio.wait_for(process.wait(), timeout=0.5)
                        except TimeoutError:
                            # Total fragments + merged file + diagnostics, not just a single stream.
                            total = await asyncio.to_thread(directory_bytes, directory)
                            if total > s.max_upload_bytes * 4 + 10_000_000:
                                raise MediaError("too_large") from None
            except TimeoutError as exc:
                raise MediaError("timeout") from exc
            finally:
                # Kill the entire process group, including FFmpeg, on cancellation or deadline.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
        result_path = directory / "result.json"
        if not result_path.exists():
            raise MediaError("internal")
        result = json.loads(result_path.read_text())
        if not result.get("ok"):
            raise MediaError(result.get("code", "internal"), result.get("reason", "unspecified"))
        return result["data"]

    async def probe(self, url: str, directory: Path) -> MediaInfo:
        return MediaInfo.model_validate(await self.run(url, directory))

    async def download(self, url: str, selection: Selection, directory: Path) -> Artifact:
        artifact = Artifact.model_validate(await self.run(url, directory, selection))
        await asyncio.to_thread(
            validate_artifact, artifact, directory, self.settings.max_upload_bytes
        )
        return artifact
