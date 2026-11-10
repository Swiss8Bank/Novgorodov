"""A single animated /start card, with bot-specific Telegram file_id caching."""

import asyncio
import hashlib
import logging
from functools import lru_cache
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest, TelegramEntityTooLarge
from aiogram.types import FSInputFile, Message
from redis.exceptions import LockError, RedisError

from lovi.store import Store
from lovi.ui import WELCOME, home_keyboard

logger = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def _digest(path: str, mtime_ns: int, size: int) -> str:
    # Cache by file version; don't re-read the GIF for each /start.
    with open(path, "rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def animation_asset(path: Path, limit: int) -> tuple[Path, str] | None:
    try:
        if not path.is_file() or path.suffix.lower() not in {".gif", ".mp4"}:
            return None
        stat = path.stat()
        if not 0 < stat.st_size <= min(limit, 50_000_000):
            return None
        with path.open("rb") as source:
            header = source.read(12)
        is_gif = path.suffix.lower() == ".gif" and header[:6] in {b"GIF87a", b"GIF89a"}
        is_mp4 = path.suffix.lower() == ".mp4" and header[4:8] == b"ftyp"
        if not (is_gif or is_mp4):
            return None
        return path, _digest(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


async def _send(message: Message, source) -> Message | None:
    try:
        return await message.answer_animation(
            animation=source,
            caption=WELCOME,
            reply_markup=home_keyboard(),
        )
    except (TelegramBadRequest, TelegramEntityTooLarge, OSError):
        # A broken or rejected optional animation must not break /start.
        logger.warning("Welcome animation rejected")
        return None


async def _send_cached(message: Message, store: Store, key: str) -> bool:
    value = await store.redis.get(key)
    if not value:
        return False
    sent = await _send(message, value.decode())
    if sent is not None:
        return True
    await store.redis.delete(key)
    return False


async def send_welcome(message: Message, store: Store) -> None:
    settings = store.settings
    if settings.welcome_animation_file_id:
        if await _send(message, settings.welcome_animation_file_id) is not None:
            return

    asset = await asyncio.to_thread(
        animation_asset,
        settings.welcome_animation_path,
        settings.max_upload_bytes,
    )
    if asset:
        path, digest = asset
        key = store.key(f"welcome-animation:{digest}")
        if await _send_cached(message, store, key):
            return
        lock = store.redis.lock(
            key + ":lock", timeout=settings.telegram_timeout_seconds + 30, blocking_timeout=10
        )
        acquired = await lock.acquire()
        if acquired:
            try:
                if await _send_cached(message, store, key):
                    return
                sent = await _send(message, FSInputFile(path))
                if sent is not None:
                    if sent.animation:
                        try:
                            await store.redis.set(
                                key, sent.animation.file_id, ex=settings.cache_ttl_seconds
                            )
                        except RedisError:
                            # The greeting was already delivered; don't duplicate it.
                            logger.warning("Welcome animation cache write failed")
                    return
            finally:
                try:
                    await lock.release()
                except LockError:
                    logger.warning("Welcome animation lock expired")
    await message.answer(WELCOME, reply_markup=home_keyboard())
