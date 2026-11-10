import base64
from datetime import UTC, datetime

from aiogram import Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, FSInputFile, Message, MessageEntity, Update, User

from lovi.handlers import make_router
from lovi.ui import WELCOME
from lovi.welcome import animation_asset


def start_update(update_id):
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(UTC),
            chat=Chat(id=1, type="private"),
            from_user=User(id=1, is_bot=False, first_name="User"),
            text="/start",
            entities=[MessageEntity(type="bot_command", offset=0, length=6)],
        ),
    )


def prepare(store):
    path = store.settings.data_dir / "welcome.gif"
    path.write_bytes(base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"))
    store.settings.welcome_animation_path = path
    dp = Dispatcher(store=store)
    dp.include_router(make_router())
    return dp, path


async def test_start_sends_one_animation_with_caption_buttons_and_cached_id(gateway_bot, store):
    dp, path = prepare(store)
    await dp.feed_update(gateway_bot, start_update(1))
    first = gateway_bot.session.calls[-1]
    assert first.__api_method__ == "sendAnimation"
    assert isinstance(first.animation, FSInputFile)
    assert first.caption == WELCOME
    assert first.reply_markup.inline_keyboard[0][0].callback_data == "help"
    assert len(gateway_bot.session.calls) == 1
    await dp.feed_update(gateway_bot, start_update(2))
    second = gateway_bot.session.calls[-1]
    assert second.animation == "welcome-animation-id"
    assert second.caption == WELCOME
    assert len(gateway_bot.session.calls) == 2


async def test_changed_gif_is_uploaded_instead_of_using_old_id(gateway_bot, store):
    dp, path = prepare(store)
    await dp.feed_update(gateway_bot, start_update(1))
    path.write_bytes(path.read_bytes() + b"changed")
    await dp.feed_update(gateway_bot, start_update(2))
    assert isinstance(gateway_bot.session.calls[-1].animation, FSInputFile)


async def test_configured_file_id_needs_no_local_asset(gateway_bot, store):
    dp, path = prepare(store)
    path.unlink()
    store.settings.welcome_animation_file_id = "configured-animation-id"
    await dp.feed_update(gateway_bot, start_update(1))
    assert gateway_bot.session.calls[-1].animation == "configured-animation-id"


async def test_telegram_rejection_falls_back_to_working_text_menu(gateway_bot, store):
    dp, path = prepare(store)
    original = gateway_bot.session.make_request

    async def reject(bot, method, timeout=None):  # noqa: ASYNC109 -- aiogram session interface
        if method.__api_method__ == "sendAnimation":
            gateway_bot.session.calls.append(method)
            raise TelegramBadRequest(method=method, message="animation rejected")
        return await original(bot, method, timeout=timeout)

    gateway_bot.session.make_request = reject
    await dp.feed_update(gateway_bot, start_update(1))
    fallback = gateway_bot.session.calls[-1]
    assert fallback.__api_method__ == "sendMessage"
    assert fallback.text == WELCOME
    assert fallback.reply_markup.inline_keyboard[0][0].callback_data == "help"


async def test_expired_cached_id_reuploads_asset(gateway_bot, store):
    dp, path = prepare(store)
    _, digest = animation_asset(path, store.settings.max_upload_bytes)
    key = store.key(f"welcome-animation:{digest}")
    await store.redis.set(key, "invalid-id")
    original = gateway_bot.session.make_request

    async def reject(bot, method, timeout=None):  # noqa: ASYNC109 -- aiogram session interface
        if method.__api_method__ == "sendAnimation" and method.animation == "invalid-id":
            gateway_bot.session.calls.append(method)
            raise TelegramBadRequest(method=method, message="wrong file identifier")
        return await original(bot, method, timeout=timeout)

    gateway_bot.session.make_request = reject
    await dp.feed_update(gateway_bot, start_update(1))
    assert len(gateway_bot.session.calls) == 2
    assert isinstance(gateway_bot.session.calls[-1].animation, FSInputFile)
    assert await store.redis.get(key) == b"welcome-animation-id"


async def test_static_preview_is_not_sent_as_a_gif(gateway_bot, store):
    dp, path = prepare(store)
    path.write_bytes(b"\x89PNG\r\n\x1a\npreview")
    await dp.feed_update(gateway_bot, start_update(1))
    assert gateway_bot.session.calls[-1].__api_method__ == "sendMessage"
    assert len(gateway_bot.session.calls) == 1
