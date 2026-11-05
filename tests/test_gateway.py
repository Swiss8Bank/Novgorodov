from datetime import UTC, datetime

from aiogram import Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Update, User
from arq.jobs import Job

from lovi.handlers import make_router
from lovi.models import MediaSession


def message(text, *, user_id=1, message_id=10):
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="User"),
        text=text,
        entities=[MessageEntity(type="bot_command", offset=0, length=6)]
        if text == "/start"
        else None,
    )


async def test_start_renders_menu(gateway_bot, store):
    dp = Dispatcher(store=store)
    dp.include_router(make_router())
    await dp.feed_update(gateway_bot, Update(update_id=1, message=message("/start")))
    response = gateway_bot.session.calls[-1]
    assert "ЛОВИ" in response.text
    assert response.reply_markup.inline_keyboard[0][0].callback_data == "help"


async def test_link_enqueues_real_arq_job(gateway_bot, store):
    dp = Dispatcher(store=store)
    dp.include_router(make_router())
    await dp.feed_update(
        gateway_bot, Update(update_id=2, message=message("https://youtu.be/dQw4w9WgXcQ"))
    )
    jobs = await store.redis.zrange(store.key("queue"), 0, -1)
    assert len(jobs) == 1
    job = await Job(jobs[0].decode(), store.redis).info()
    assert job.function == "inspect_media"
    assert job.args[:2] == (1, 1)
    assert job.args[3] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert await store.redis.get(store.key("active:1")) == jobs[0]


async def test_valid_button_enqueues_delivery(gateway_bot, store, info):
    dp = Dispatcher(store=store)
    dp.include_router(make_router())
    sid = await store.save_session(MediaSession(user_id=1, chat_id=1, info=info))
    callback = CallbackQuery(
        id="cb1",
        from_user=User(id=1, is_bot=False, first_name="User"),
        chat_instance="instance",
        message=message("card"),
        data=f"get:{sid}:audio",
    )
    await dp.feed_update(gateway_bot, Update(update_id=3, callback_query=callback))
    jobs = await store.redis.zrange(store.key("queue"), 0, -1)
    job = await Job(jobs[0].decode(), store.redis).info()
    assert job.function == "deliver_media"
    assert job.args[3:5] == (sid, "audio")


async def test_foreign_button_never_queues(gateway_bot, store, info):
    dp = Dispatcher(store=store)
    dp.include_router(make_router())
    sid = await store.save_session(MediaSession(user_id=999, chat_id=999, info=info))
    callback = CallbackQuery(
        id="cb2",
        from_user=User(id=1, is_bot=False, first_name="User"),
        chat_instance="instance",
        message=message("card"),
        data=f"get:{sid}:v720",
    )
    await dp.feed_update(gateway_bot, Update(update_id=4, callback_query=callback))
    assert await store.redis.zcard(store.key("queue")) == 0
    assert "устарела" in gateway_bot.session.calls[-1].text
