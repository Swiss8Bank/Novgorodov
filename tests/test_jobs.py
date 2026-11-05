import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendVideo

from lovi.extractor import MediaError
from lovi.jobs import deliver_media, inspect_media
from lovi.models import Artifact, MediaSession, Selection


def context(store, fake_bot, settings, downloader):
    return {"store": store, "bot": fake_bot, "settings": settings, "downloader": downloader}


async def test_probe_card_and_metadata_cache(store, fake_bot, settings, info):
    downloader = SimpleNamespace(probe=AsyncMock(return_value=info))
    ctx = context(store, fake_bot, settings, downloader)
    await store.admit(1, "one")
    await inspect_media(ctx, 1, 1, 1, info.url, "one")
    await inspect_media(ctx, 2, 2, 2, info.url, "two")
    assert downloader.probe.await_count == 1
    assert await store.redis.get(store.key("active:1")) is None
    call = fake_bot.edit_message_text.call_args
    assert "Нашёл" in call.kwargs["text"]
    assert "reply_markup" in call.kwargs
    assert not list(settings.data_dir.iterdir())


@pytest.mark.parametrize(
    ("key", "suffix", "method"),
    [
        ("v720", ".mp4", "send_video"),
        ("audio", ".mp3", "send_audio"),
        ("cover", ".jpg", "send_document"),
    ],
)
async def test_delivery_cache_and_temp_cleanup(
    store, fake_bot, settings, info, key, suffix, method
):
    async def download(url, selection, directory):
        path = directory / f"media{suffix}"
        path.write_bytes(b"media-fixture")
        return Artifact(path=str(path), kind=selection.kind)

    downloader = SimpleNamespace(download=AsyncMock(side_effect=download))
    ctx = context(store, fake_bot, settings, downloader)
    sid1 = await store.save_session(MediaSession(user_id=1, chat_id=1, info=info))
    sid2 = await store.save_session(MediaSession(user_id=2, chat_id=2, info=info))
    await deliver_media(ctx, 1, 1, 10, sid1, key, "first")
    await deliver_media(ctx, 2, 2, 11, sid2, key, "second")
    assert downloader.download.await_count == 1
    sender = getattr(fake_bot, method)
    assert sender.await_count == 2
    field = {"v720": "video", "audio": "audio", "cover": "document"}[key]
    assert isinstance(sender.call_args.kwargs[field], str)
    assert not list(settings.data_dir.iterdir())


async def test_download_failure_releases_user_and_files(store, fake_bot, settings, info):
    async def fail(url, selection, directory):
        (directory / "media.part").write_bytes(b"partial")
        raise MediaError("too_large")

    downloader = SimpleNamespace(download=AsyncMock(side_effect=fail))
    ctx = context(store, fake_bot, settings, downloader)
    sid = await store.save_session(MediaSession(user_id=1, chat_id=1, info=info))
    await store.admit(1, "first")
    await deliver_media(ctx, 1, 1, 10, sid, "v720", "first")
    assert await store.redis.get(store.key("active:1")) is None
    assert not list(settings.data_dir.iterdir())
    assert "слишком большой" in fake_bot.edit_message_text.call_args.kwargs["text"]
    fake_bot.send_video.assert_not_awaited()


async def test_foreign_session_cannot_download(store, fake_bot, settings, info):
    downloader = SimpleNamespace(download=AsyncMock())
    ctx = context(store, fake_bot, settings, downloader)
    sid = await store.save_session(MediaSession(user_id=1, chat_id=1, info=info))
    await deliver_media(ctx, 2, 2, 10, sid, "v720", "forged")
    downloader.download.assert_not_awaited()


async def test_simultaneous_download_is_deduplicated(store, fake_bot, settings, info):
    async def download(url, selection, directory):
        await asyncio.sleep(0.05)
        path = directory / "media.mp4"
        path.write_bytes(b"video")
        return Artifact(path=str(path), kind="video")

    downloader = SimpleNamespace(download=AsyncMock(side_effect=download))
    ctx = context(store, fake_bot, settings, downloader)
    sids = [await store.save_session(MediaSession(user_id=i, chat_id=i, info=info)) for i in (1, 2)]
    await asyncio.gather(
        *(
            deliver_media(ctx, i, i, 10, sid, "v720", f"job-{i}")
            for i, sid in zip((1, 2), sids, strict=True)
        )
    )
    assert downloader.download.await_count == 1
    assert fake_bot.send_video.await_count == 2


async def test_invalid_cached_file_gets_refreshed(store, fake_bot, settings, info):
    from lovi.models import CachedFile

    await store.save_file(
        info.url, Selection(kind="video", height=720), CachedFile(kind="video", file_id="expired")
    )

    async def download(url, selection, directory):
        path = directory / "media.mp4"
        path.write_bytes(b"video")
        return Artifact(path=str(path), kind="video")

    fake_bot.send_video.side_effect = [
        TelegramBadRequest(
            method=SendVideo(chat_id=1, video="expired"),
            message="wrong file identifier/HTTP URL specified",
        ),
        SimpleNamespace(video=SimpleNamespace(file_id="new-file-id")),
    ]
    downloader = SimpleNamespace(download=AsyncMock(side_effect=download))
    sid = await store.save_session(MediaSession(user_id=1, chat_id=1, info=info))
    await deliver_media(
        context(store, fake_bot, settings, downloader), 1, 1, 10, sid, "v720", "refresh"
    )
    assert downloader.download.await_count == 1
    cached = await store.cached_file(info.url, Selection(kind="video", height=720))
    assert cached.file_id == "new-file-id"
