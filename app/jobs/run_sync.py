import logging
import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.logging import setup_logging
from app.services.sync_service import run_repository_sync

logger = logging.getLogger(__name__)


def _parse_cron(expr: str) -> dict:
    """Parse a standard 5-field cron expression into APScheduler kwargs."""
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5-field cron expression, got: {expr!r}")

    keys = ["minute", "hour", "day", "month", "day_of_week"]
    return dict(zip(keys, parts))


def main() -> None:
    setup_logging()
    logger.info("scheduler_starting", extra={"cron": settings.sync_cron})

    # Run once immediately on startup
    logger.info("initial_sync_start")
    try:
        run_repository_sync()
    except Exception:
        logger.exception("initial_sync_failed")

    cron_kwargs = _parse_cron(settings.sync_cron)
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        run_repository_sync,
        trigger=CronTrigger(**cron_kwargs),
        id="repo_sync",
        name="GitHub Review Sync",
        max_instances=1,
        replace_existing=True,
    )

    def _shutdown(signum, frame):
        logger.info("scheduler_shutdown", extra={"signal": signum})
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("scheduler_running", extra={"cron": settings.sync_cron})
    scheduler.start()


if __name__ == "__main__":
    main()
