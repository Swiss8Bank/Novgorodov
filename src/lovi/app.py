import asyncio
import secrets
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from arq.connections import ArqRedis, RedisSettings, create_pool

from lovi.config import Settings, get_settings
from lovi.handlers import make_router
from lovi.middleware import InputThrottle
from lovi.store import Store
from lovi.telegram import make_bot, setup_logging


async def create_runtime(settings: Settings) -> tuple[Bot, Dispatcher, ArqRedis]:
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    bot = make_bot(settings, redis)
    dispatcher = Dispatcher(store=Store(redis, settings))
    dispatcher.update.outer_middleware(InputThrottle(redis, settings.namespace))
    dispatcher.include_router(make_router())
    return bot, dispatcher, redis


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="✦ Открыть ЛОВИ"),
            BotCommand(command="help", description="Как скачать ролик"),
        ]
    )
    await bot.set_my_description(
        "ЛОВИ ✦ Видео, MP3 и обложки по ссылке. YouTube, Shorts, TikTok и Instagram Reels."
    )
    await bot.set_my_short_description("ЛОВИ ✦ Одна ссылка. Пара нажатий. Готово.")


POLL_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""
POLL_RENEW = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('EXPIRE', KEYS[1], 60) end
return 0
"""


async def run_polling() -> None:
    settings = get_settings()
    bot, dispatcher, redis = await create_runtime(settings)
    owner = secrets.token_hex(16)
    key = f"{settings.namespace}:polling"
    renew = None
    try:
        if not await redis.set(key, owner, nx=True, ex=60):
            raise RuntimeError("Уже запущен polling gateway. Масштабируйте worker, а не bot.")
        polling_task = asyncio.current_task()

        async def renew_lease():
            try:
                while True:
                    await asyncio.sleep(20)
                    if not await redis.eval(POLL_RENEW, 1, key, owner):
                        break
            except Exception:
                pass
            # Prevent competing pollers if the lease can no longer be renewed.
            polling_task.cancel()

        renew = asyncio.create_task(renew_lease())
        await set_commands(bot)
        await bot.delete_webhook(drop_pending_updates=False)
        await dispatcher.start_polling(
            bot,
            close_bot_session=False,
            tasks_concurrency_limit=64,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        if renew:
            renew.cancel()
            with suppress(asyncio.CancelledError):
                await renew
        with suppress(Exception):
            await redis.eval(POLL_RELEASE, 1, key, owner)
        await bot.session.close()
        await redis.aclose()


async def create_web_app(settings: Settings) -> web.Application:
    bot, dispatcher, redis = await create_runtime(settings)
    app = web.Application(client_max_size=1024 * 1024)
    # Wait for durable queue admission BEFORE returning 200 to Telegram.
    SimpleRequestHandler(
        dispatcher=dispatcher,
        bot=bot,
        handle_in_background=False,
        secret_token=settings.webhook_secret.get_secret_value(),
    ).register(app, path="/telegram")
    setup_application(app, dispatcher, bot=bot)

    heartbeat = None
    heartbeat_key = f"{settings.namespace}:webhook_health"

    async def startup(_):
        nonlocal heartbeat
        await set_commands(bot)
        await bot.set_webhook(
            settings.webhook_base_url.rstrip("/") + "/telegram",
            secret_token=settings.webhook_secret.get_secret_value(),
            allowed_updates=dispatcher.resolve_used_update_types(),
            drop_pending_updates=False,
        )

        async def beat():
            while True:
                await redis.set(heartbeat_key, "1", ex=45)
                await asyncio.sleep(15)

        heartbeat = asyncio.create_task(beat())

    async def cleanup(_):
        if heartbeat:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        with suppress(Exception):
            await redis.delete(heartbeat_key)
        await bot.session.close()
        await redis.aclose()

    async def health(_):
        try:
            await redis.ping()
            worker_alive = bool(await redis.exists(f"{settings.namespace}:worker_health"))
        except Exception:
            worker_alive = False
        return web.json_response({"ok": worker_alive}, status=200 if worker_alive else 503)

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    app.router.add_get("/health", health)
    return app


def main() -> None:
    setup_logging()
    settings = get_settings()
    if settings.bot_mode == "polling":
        asyncio.run(run_polling())
    else:
        web.run_app(
            create_web_app(settings),
            host=settings.webhook_host,
            port=settings.webhook_port,
            access_log=None,
        )
