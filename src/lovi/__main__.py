import asyncio
import sys

from arq import run_worker
from arq.connections import RedisSettings, create_pool

from lovi.config import get_settings


async def health(gateway: bool = False) -> bool:
    settings = get_settings()
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await redis.ping()
        worker = bool(await redis.exists(f"{settings.namespace}:worker_health"))
        if gateway:
            key = "polling" if settings.bot_mode == "polling" else "webhook_health"
            return worker and bool(await redis.exists(f"{settings.namespace}:{key}"))
        return worker
    finally:
        await redis.aclose()


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "bot"
    if command == "bot":
        from lovi.app import main as run_bot

        run_bot()
    elif command == "worker":
        from lovi.telegram import setup_logging
        from lovi.worker import WorkerSettings

        setup_logging()
        s = get_settings()
        run_worker(
            WorkerSettings,
            redis_settings=RedisSettings.from_dsn(s.redis_url),
            queue_name=f"{s.namespace}:queue",
            max_jobs=s.worker_concurrency,
            job_timeout=s.job_timeout_seconds,
            health_check_key=f"{s.namespace}:worker_health",
            job_completion_wait=30,
        )
    elif command == "health":
        sys.exit(0 if asyncio.run(health(gateway="gateway" in sys.argv[2:])) else 1)
    elif command == "diagnose":
        import argparse

        from lovi.diagnostics import diagnose, print_report

        parser = argparse.ArgumentParser(prog="python -m lovi diagnose")
        parser.add_argument("url")
        modes = parser.add_mutually_exclusive_group()
        modes.add_argument("--cdn", action="store_true")
        modes.add_argument("--download", action="store_true")
        parser.add_argument(
            "--height", type=int, default=720, choices=[0, 240, 360, 480, 720, 1080, 1440, 2160]
        )
        args = parser.parse_args(sys.argv[2:])
        report = asyncio.run(
            diagnose(
                get_settings(),
                args.url,
                check_cdn=args.cdn,
                check_download=args.download,
                height=args.height,
            )
        )
        print_report(report)
        sys.exit(0 if report["ok"] else 1)
    else:
        raise SystemExit("Использование: python -m lovi [bot|worker|health|diagnose URL]")


if __name__ == "__main__":
    main()
