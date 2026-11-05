from aiogram import BaseMiddleware


class InputThrottle(BaseMiddleware):
    """Discard floods before creating replies or jobs, including invalid links and commands."""

    def __init__(self, redis, namespace):
        self.redis, self.namespace = redis, namespace

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user:
            accepted = await self.redis.set(f"{self.namespace}:input:{user.id}", "1", nx=True, ex=1)
            if not accepted:
                return None
        return await handler(event, data)
