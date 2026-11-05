import shutil
import time

from lovi.config import get_settings
from lovi.downloader import Downloader
from lovi.jobs import deliver_media, inspect_media
from lovi.store import Store
from lovi.telegram import make_bot, setup_logging


async def startup(ctx) -> None:
    setup_logging()
    settings = get_settings()
    for executable in ("ffmpeg", "ffprobe", settings.js_runtime):
        if not shutil.which(executable):
            raise RuntimeError(f"Не установлен {executable}; смотрите README")
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Reap only our own abandoned temp directories, after all possible lease/job deadlines.
    cutoff = time.time() - settings.job_timeout_seconds * 3 - 600
    for directory in settings.data_dir.iterdir():
        if directory.is_dir() and directory.name.startswith(("probe-", "download-")):
            if directory.stat().st_mtime < cutoff:
                shutil.rmtree(directory)
    ctx.update(
        settings=settings,
        store=Store(ctx["redis"], settings),
        downloader=Downloader(settings),
        bot=make_bot(settings, ctx["redis"]),
    )


async def shutdown(ctx) -> None:
    if "bot" in ctx:
        await ctx["bot"].session.close()


class WorkerSettings:
    functions = [inspect_media, deliver_media]
    on_startup = startup
    on_shutdown = shutdown
    max_tries = 1
    keep_result = 0
    # Sending to Telegram isn't transactional: don't replay possibly delivered files after a crash.
    retry_jobs = False
    health_check_interval = 15
