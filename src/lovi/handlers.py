import logging
import secrets

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from lovi.models import Selection
from lovi.store import Store
from lovi.ui import HELP, home_keyboard
from lovi.urls import InvalidLink, normalize_url
from lovi.welcome import send_welcome

logger = logging.getLogger(__name__)

ADMISSION_ERRORS = {
    "busy": "Уже работаю с твоим запросом. Дождись результата ✦",
    "cooldown": "Дай мне пару секунд и попробуй снова ✦",
    "full": "Сейчас много запросов. Попробуй через минуту ✦",
    "quota": "Часовой лимит исчерпан. Вернись чуть позже ✦",
}


async def enqueue(store: Store, function: str, args: list, request_id: str) -> None:
    job = await store.redis.enqueue_job(
        function,
        *args,
        request_id,
        _job_id=request_id,
        _queue_name=store.key("queue"),
        _expires=store.settings.job_timeout_seconds,
    )
    if job is None:
        raise RuntimeError("Queue rejected request")


def make_router() -> Router:
    router = Router()
    router.message.filter(F.chat.type == "private")

    @router.message(CommandStart())
    async def start(message: Message, store: Store) -> None:
        await send_welcome(message, store)

    @router.message(Command("help"))
    async def help_message(message: Message) -> None:
        await message.answer(HELP)

    @router.callback_query(F.data == "help")
    async def help_callback(callback: CallbackQuery) -> None:
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.answer(HELP)

    @router.callback_query(F.data.startswith("get:"))
    async def select(callback: CallbackQuery, store: Store) -> None:
        if not isinstance(callback.message, Message) or callback.message.chat.type != "private":
            await callback.answer("Открой личный чат с ботом.", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) != 3:
            await callback.answer("Неизвестная кнопка.")
            return
        _, session_id, key = parts
        chat_id, user_id = callback.message.chat.id, callback.from_user.id
        session = await store.session(session_id, user_id, chat_id)
        try:
            selection = Selection.from_key(key)
        except ValueError:
            await callback.answer("Неизвестный формат.", show_alert=True)
            return
        if session is None or not session.permits(selection):
            await callback.answer("Карточка устарела. Пришли ссылку заново ✦", show_alert=True)
            return
        request_id = secrets.token_hex(12)
        result = await store.admit(user_id, request_id)
        if result != "ok":
            await callback.answer(ADMISSION_ERRORS[result], show_alert=True)
            return
        try:
            await callback.answer("Лови, запрос в очереди ✦")
            progress = await callback.message.answer("<b>ЛОВИ ✦</b>\n\n◌ В очереди на скачивание…")
            await enqueue(
                store,
                "deliver_media",
                [user_id, chat_id, progress.message_id, session_id, key],
                request_id,
            )
        except Exception:
            await store.release(user_id, request_id)
            raise

    @router.message(F.text)
    async def link(message: Message, store: Store) -> None:
        try:
            url = normalize_url(message.text or "")
        except InvalidLink as exc:
            await message.answer(f"<b>ЛОВИ ✦</b>\n\n{exc}", reply_markup=home_keyboard())
            return
        user_id, chat_id = message.from_user.id, message.chat.id
        request_id = secrets.token_hex(12)
        result = await store.admit(user_id, request_id)
        if result != "ok":
            await message.answer(ADMISSION_ERRORS[result])
            return
        try:
            progress = await message.answer(
                "<b>ЛОВИ ✦</b>\n\n◌ Ссылка принята. Проверю форматы по очереди…"
            )
            await enqueue(
                store, "inspect_media", [user_id, chat_id, progress.message_id, url], request_id
            )
        except Exception:
            await store.release(user_id, request_id)
            raise

    @router.message()
    async def other(message: Message) -> None:
        await message.answer("Пришли текстовую ссылку на ролик — и я его поймаю ✦")

    @router.errors()
    async def error(event) -> bool:
        # Don't dump exception strings: upstream URLs may contain authentication data.
        logger.error("Update failed: %s", type(event.exception).__name__)
        message = event.update.message
        callback = event.update.callback_query
        try:
            if callback:
                await callback.answer(
                    "Временно не могу принять запрос. Попробуй позже.", show_alert=True
                )
            elif message:
                await message.answer("Сервис временно недоступен. Попробуй чуть позже ☁️")
        except Exception:
            pass
        return True

    return router
