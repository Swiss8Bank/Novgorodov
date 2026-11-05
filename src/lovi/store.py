import hashlib
import secrets

from arq.connections import ArqRedis

from lovi.config import Settings
from lovi.models import CachedFile, MediaInfo, MediaSession, Selection

# Redis TIME avoids clock differences between gateways. All admission decisions are atomic.
ADMIT = """
local t = redis.call('TIME')
local now = tonumber(t[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('EXISTS', KEYS[2]) == 1 then return 'busy' end
if redis.call('EXISTS', KEYS[3]) == 1 then return 'cooldown' end
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then return 'full' end
if tonumber(redis.call('GET', KEYS[4]) or '0') >= tonumber(ARGV[3]) then return 'quota' end
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[4])
redis.call('SET', KEYS[3], '1', 'EX', ARGV[5])
local count = redis.call('INCR', KEYS[4])
if count == 1 then redis.call('EXPIRE', KEYS[4], 3600) end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[4]), ARGV[1])
return 'ok'
"""

RELEASE = """
redis.call('ZREM', KEYS[1], ARGV[1])
if redis.call('GET', KEYS[2]) == ARGV[1] then redis.call('DEL', KEYS[2]) end
return 1
"""


class Store:
    def __init__(self, redis: ArqRedis, settings: Settings):
        self.redis, self.settings = redis, settings
        self.prefix = settings.namespace

    def key(self, part: str) -> str:
        return f"{self.prefix}:{part}"

    async def admit(self, user_id: int, job_id: str) -> str:
        s = self.settings
        result = await self.redis.eval(
            ADMIT,
            4,
            self.key("pending"),
            self.key(f"active:{user_id}"),
            self.key(f"cooldown:{user_id}"),
            self.key(f"quota:{user_id}"),
            job_id,
            s.max_queue_size,
            s.max_jobs_per_user_hour,
            # Queue wait is bounded by arq expiry; reserve through execution as well.
            s.job_timeout_seconds * 2 + 120,
            s.link_cooldown_seconds,
        )
        return result.decode() if isinstance(result, bytes) else str(result)

    async def release(self, user_id: int, job_id: str) -> None:
        await self.redis.eval(
            RELEASE,
            2,
            self.key("pending"),
            self.key(f"active:{user_id}"),
            job_id,
        )

    async def save_session(self, session: MediaSession) -> str:
        session_id = secrets.token_urlsafe(9)
        await self.redis.set(
            self.key(f"session:{session_id}"),
            session.model_dump_json(),
            ex=self.settings.session_ttl_seconds,
        )
        return session_id

    async def session(self, session_id: str, user_id: int, chat_id: int) -> MediaSession | None:
        raw = await self.redis.get(self.key(f"session:{session_id}"))
        if not raw:
            return None
        session = MediaSession.model_validate_json(raw)
        return session if session.user_id == user_id and session.chat_id == chat_id else None

    @staticmethod
    def digest(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def file_key(self, url: str, selection: Selection) -> str:
        return self.key(f"file:{self.digest(url)}:{selection.key}")

    async def cached_file(self, url: str, selection: Selection) -> CachedFile | None:
        raw = await self.redis.get(self.file_key(url, selection))
        return CachedFile.model_validate_json(raw) if raw else None

    async def save_file(self, url: str, selection: Selection, file: CachedFile) -> None:
        await self.redis.set(
            self.file_key(url, selection),
            file.model_dump_json(),
            ex=self.settings.cache_ttl_seconds,
        )

    async def cached_info(self, url: str) -> MediaInfo | None:
        raw = await self.redis.get(self.key(f"info:{self.digest(url)}"))
        return MediaInfo.model_validate_json(raw) if raw else None

    async def save_info(self, info: MediaInfo) -> None:
        await self.redis.set(
            self.key(f"info:{self.digest(info.url)}"),
            info.model_dump_json(),
            ex=900,
        )
