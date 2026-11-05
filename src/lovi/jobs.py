import logging
import tempfile
import time
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import FSInputFile
from redis.exceptions import LockError

from lovi.downloader import Downloader
from lovi.extractor import MediaError
from lovi.models import CachedFile, MediaSession, Selection
from lovi.store import Store
from lovi.ui import delivery_caption, error_text, media_card, media_keyboard

logger = logging.getLogger(__name__)


async def edit_status(bot: Bot, chat_id: int, message_id: int, text: str, **kwargs) -> None:
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, **kwargs)
    except TelegramBadRequest:
        # Status messages can be deleted; this must not lose the downloaded media.
        pass


async def inspect_media(
    ctx, user_id: int, chat_id: int, message_id: int, url: str, request_id: str
) -> None:
    store: Store = ctx["store"]
    downloader: Downloader = ctx["downloader"]
    bot: Bot = ctx["bot"]
    started = time.monotonic()
    outcome = "ok"
    try:
        await edit_status(
            bot, chat_id, message_id, "<b>ЛОВИ ✦</b>\n\n🔎 Смотрю, какие есть форматы…"
        )
        info = await store.cached_info(url)
        if not info:
            with tempfile.TemporaryDirectory(dir=ctx["settings"].data_dir, prefix="probe-") as work:
                info = await downloader.probe(url, Path(work))
            await store.save_info(info)
        session_id = await store.save_session(
            MediaSession(user_id=user_id, chat_id=chat_id, info=info)
        )
        await edit_status(
            bot,
            chat_id,
            message_id,
            media_card(info),
            reply_markup=media_keyboard(info, session_id),
        )
    except TelegramForbiddenError:
        outcome = "blocked"
    except Exception as exc:
        outcome = exc.code if isinstance(exc, MediaError) else "internal"
        await report_failure(bot, chat_id, message_id, outcome)
    finally:
        await store.release(user_id, request_id)
        logger.info(
            "probe job=%s outcome=%s seconds=%.1f", request_id, outcome, time.monotonic() - started
        )


async def report_failure(bot: Bot, chat_id: int, message_id: int, code: str) -> None:
    try:
        await edit_status(bot, chat_id, message_id, error_text(code))
    except Exception:
        logger.warning("Failed to send status; code=%s", code)


async def send_media(
    bot: Bot, chat_id: int, media, selection: Selection, info, artifact=None, as_document=False
):
    common = {"chat_id": chat_id, "caption": delivery_caption(info, selection)}
    if selection.kind == "video" and (as_document or (artifact and artifact.video_codec != "h264")):
        message = await bot.send_document(document=media, **common)
        return CachedFile(kind="video", file_id=message.document.file_id, as_document=True)
    if selection.kind == "audio":
        message = await bot.send_audio(
            audio=media,
            title=info.title[:180],
            performer=info.author[:100] or "ЛОВИ",
            duration=info.duration,
            **common,
        )
        return CachedFile(kind="audio", file_id=message.audio.file_id)
    if selection.kind == "cover":
        # A document preserves the original cover quality and bypasses photo dimension limits.
        message = await bot.send_document(document=media, **common)
        return CachedFile(kind="cover", file_id=message.document.file_id)
    message = await bot.send_video(
        video=media,
        supports_streaming=True,
        duration=info.duration,
        width=artifact.width if artifact else None,
        height=artifact.height if artifact else None,
        **common,
    )
    return CachedFile(kind="video", file_id=message.video.file_id)


async def deliver_media(
    ctx,
    user_id: int,
    chat_id: int,
    message_id: int,
    session_id: str,
    selection_key: str,
    request_id: str,
) -> None:
    store: Store = ctx["store"]
    bot: Bot = ctx["bot"]
    settings = ctx["settings"]
    started = time.monotonic()
    outcome = "ok"
    try:
        session = await store.session(session_id, user_id, chat_id)
        selection = Selection.from_key(selection_key)
        if session is None or not session.permits(selection):
            raise MediaError("format")
        info = session.info
        key = store.file_key(info.url, selection)
        await edit_status(
            bot,
            chat_id,
            message_id,
            "<b>ЛОВИ ✦</b>\n\n⚡ Готовлю файл. Можно пока оставить чат открытым.",
        )
        # Deduplicate downloads across worker processes. Redis stores only Telegram IDs afterward.
        lock = store.redis.lock(
            f"{key}:lock", timeout=settings.job_timeout_seconds + 30, blocking_timeout=20
        )
        acquired = await lock.acquire()
        if not acquired:
            raise MediaError("timeout")
        try:
            cached = await store.cached_file(info.url, selection)
            if cached:
                try:
                    await send_media(
                        bot,
                        chat_id,
                        cached.file_id,
                        selection,
                        info,
                        as_document=cached.as_document,
                    )
                    outcome = "cache"
                except TelegramBadRequest as exc:
                    if not any(
                        word in exc.message.lower()
                        for word in (
                            "wrong file",
                            "file reference",
                            "file_id",
                            "file identifier",
                        )
                    ):
                        raise
                    await store.redis.delete(key)
                    cached = None
            if not cached:
                with tempfile.TemporaryDirectory(dir=settings.data_dir, prefix="download-") as work:
                    artifact = await ctx["downloader"].download(info.url, selection, Path(work))
                    await edit_status(
                        bot, chat_id, message_id, "<b>Поймал ✦</b>\n\n📤 Отправляю файл в Telegram…"
                    )
                    media = FSInputFile(
                        artifact.path, filename=f"lovi-{selection.key}{Path(artifact.path).suffix}"
                    )
                    saved = await send_media(bot, chat_id, media, selection, info, artifact)
                    await store.save_file(info.url, selection, saved)
        finally:
            try:
                await lock.release()
            except LockError:
                pass
        await edit_status(
            bot,
            chat_id,
            message_id,
            "<b>Готово, лови ✦</b>\n\nФайл уже в чате. Сохрани его или присылай новую ссылку.",
        )
    except TelegramForbiddenError:
        outcome = "blocked"
    except Exception as exc:
        outcome = exc.code if isinstance(exc, MediaError) else "internal"
        if isinstance(exc, TelegramBadRequest) and "too large" in exc.message.lower():
            outcome = "too_large"
        await report_failure(bot, chat_id, message_id, outcome)
    finally:
        await store.release(user_id, request_id)
        logger.info(
            "delivery job=%s outcome=%s seconds=%.1f",
            request_id,
            outcome,
            time.monotonic() - started,
        )
