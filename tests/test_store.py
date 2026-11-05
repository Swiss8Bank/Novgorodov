import asyncio

from lovi.models import CachedFile, MediaSession, Selection
from lovi.store import Store


async def test_atomic_queue_bound(redis, settings):
    settings.max_queue_size = 5
    store = Store(redis, settings)
    results = await asyncio.gather(*(store.admit(i, f"job-{i}") for i in range(150)))
    assert results.count("ok") == 5
    assert results.count("full") == 145
    assert await redis.zcard(store.key("pending")) == 5


async def test_single_active_and_release_is_owner_checked(store):
    assert await store.admit(1, "job-1") == "ok"
    assert await store.admit(1, "job-2") == "busy"
    await store.release(1, "job-2")
    assert await store.admit(1, "job-3") == "busy"
    await store.release(1, "job-1")
    assert await store.admit(1, "job-4") == "cooldown"


async def test_quota_and_expired_reservations(store):
    store.settings.max_jobs_per_user_hour = 1
    assert await store.admit(1, "first") == "ok"
    await store.release(1, "first")
    await store.redis.delete(store.key("cooldown:1"))
    assert await store.admit(1, "second") == "quota"
    await store.redis.zadd(store.key("pending"), {"expired": 1})
    assert await store.admit(2, "fresh") == "ok"
    assert await store.redis.zscore(store.key("pending"), "expired") is None


async def test_session_ownership_and_expiry(store, info):
    key = await store.save_session(MediaSession(user_id=1, chat_id=2, info=info))
    assert await store.session(key, 1, 2) is not None
    assert await store.session(key, 999, 2) is None
    assert await store.session(key, 1, 999) is None
    await store.redis.delete(store.key(f"session:{key}"))
    assert await store.session(key, 1, 2) is None


async def test_cache_separates_formats_and_bots(store, info, settings, redis):
    video = Selection(kind="video", height=720)
    await store.save_file(info.url, video, CachedFile(kind="video", file_id="test-file"))
    assert (await store.cached_file(info.url, video)).file_id == "test-file"
    assert await store.cached_file(info.url, Selection(kind="audio")) is None
    other = settings.model_copy(update={"bot_token": type(settings.bot_token)("other-token")})
    assert await Store(redis, other).cached_file(info.url, video) is None


async def test_info_cache(store, info):
    await store.save_info(info)
    assert await store.cached_info(info.url) == info
