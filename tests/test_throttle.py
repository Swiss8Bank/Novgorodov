from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery

from lovi.middleware import InputThrottle
from lovi.telegram import TelegramThrottle


async def test_input_flood_is_discarded(store):
    middleware = InputThrottle(store.redis, store.prefix)
    handler = AsyncMock()
    data = {"event_from_user": SimpleNamespace(id=1)}
    for _ in range(20):
        await middleware(handler, object(), data)
    assert handler.await_count == 1


async def test_telegram_retry_after_is_honored(store):
    method = AnswerCallbackQuery(callback_query_id="test")
    request = AsyncMock(
        side_effect=[TelegramRetryAfter(method=method, message="slow", retry_after=0), True]
    )
    middleware = TelegramThrottle(store.redis, store.prefix)
    assert await middleware(request, None, method) is True
    assert request.await_count == 2


async def test_network_error_doesnt_duplicate_delivery(store):
    method = AnswerCallbackQuery(callback_query_id="test")
    request = AsyncMock(side_effect=TelegramNetworkError(method=method, message="network"))
    with pytest.raises(TelegramNetworkError):
        await TelegramThrottle(store.redis, store.prefix)(request, None, method)
    assert request.await_count == 1
