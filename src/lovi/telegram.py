import asyncio
import logging
from typing import Any

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import TelegramMethod
from aiogram.types import LinkPreviewOptions
from arq.connections import ArqRedis

from lovi.config import Settings

RATE = """
if tonumber(redis.call('GET', KEYS[1]) or '0') >= 22 then return 0 end
if ARGV[1] == '1' and redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('PEXPIRE', KEYS[1], 1000) end
if ARGV[1] == '1' then redis.call('SET', KEYS[2], '1', 'PX', 1100) end
return 1
"""


class TelegramThrottle:
    def __init__(self, redis: ArqRedis, namespace: str):
        self.redis, self.namespace = redis, namespace

    async def __call__(self, make_request, bot: Bot, method: TelegramMethod[Any]):
        if method.__api_method__ == "getUpdates":
            return await make_request(bot, method)
        chat_id = getattr(method, "chat_id", None)
        for attempt in range(4):
            while not await self.redis.eval(  # noqa: ASYNC110 -- shared Redis limiter, no local event
                RATE,
                2,
                f"{self.namespace}:tg:global",
                f"{self.namespace}:tg:chat:{chat_id}",
                "1" if chat_id is not None else "0",
            ):
                await asyncio.sleep(0.1)
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                if attempt == 3:
                    raise
                await asyncio.sleep(exc.retry_after + 0.2)
            except TelegramNetworkError:
                # Upload requests are not idempotent: don't blindly retry and send duplicates.
                raise
        raise RuntimeError("Telegram retry budget exceeded")


def make_bot(settings: Settings, redis: ArqRedis) -> Bot:
    kwargs: dict[str, Any] = {"timeout": settings.telegram_timeout_seconds}
    if settings.telegram_proxy:
        kwargs["proxy"] = settings.telegram_proxy.get_secret_value()
    if settings.telegram_api_base:
        kwargs["api"] = TelegramAPIServer.from_base(settings.telegram_api_base.rstrip("/"))
    session = AiohttpSession(**kwargs)
    session.middleware(TelegramThrottle(redis, settings.namespace))
    return Bot(
        token=settings.bot_token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(
            parse_mode="HTML",
            link_preview=LinkPreviewOptions(is_disabled=True),
        ),
    )


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # arq INFO includes job arguments (URLs); log counts/outcomes in our own worker instead.
    logging.getLogger("arq.worker").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
