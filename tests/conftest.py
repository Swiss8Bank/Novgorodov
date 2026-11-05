from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Chat, Message, User
from arq.connections import ArqRedis
from fakeredis.aioredis import FakeRedis

from lovi.config import Settings
from lovi.models import MediaInfo
from lovi.store import Store

TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"


@pytest.fixture
def settings(tmp_path):
    return Settings(bot_token=TOKEN, data_dir=tmp_path, _env_file=None)


@pytest.fixture
async def redis():
    fake = FakeRedis()
    pool = ArqRedis(connection_pool=fake.connection_pool)
    yield pool
    await pool.aclose()
    await fake.aclose()


@pytest.fixture
def store(redis, settings):
    return Store(redis, settings)


@pytest.fixture
def info():
    return MediaInfo(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="Тестовый ролик",
        author="Автор",
        platform="YouTube",
        duration=60,
        heights=[360, 720, 1080],
        has_audio=True,
        has_thumbnail=True,
    )


@pytest.fixture
def fake_bot():
    return SimpleNamespace(
        edit_message_text=AsyncMock(),
        send_video=AsyncMock(
            return_value=SimpleNamespace(video=SimpleNamespace(file_id="video-id"))
        ),
        send_audio=AsyncMock(
            return_value=SimpleNamespace(audio=SimpleNamespace(file_id="audio-id"))
        ),
        send_document=AsyncMock(
            return_value=SimpleNamespace(document=SimpleNamespace(file_id="cover-id"))
        ),
    )


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109 -- aiogram interface
        self.calls.append(method)
        if method.__api_method__ == "sendMessage":
            return Message(
                message_id=len(self.calls),
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text,
            ).as_(bot)
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""


@pytest.fixture
async def gateway_bot():
    bot = Bot(TOKEN, session=RecordingSession())
    bot._me = User(id=123456789, is_bot=True, first_name="ЛОВИ", username="lovi_test_bot")
    yield bot
    await bot.session.close()
