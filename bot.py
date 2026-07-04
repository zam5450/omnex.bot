"""
Main bot initialization and event loop.
Onboards channel owners and posts stock snapshots with owner referral links.
"""
import asyncio
import signal

from telegram.error import Conflict
from telegram.ext import Application

from utils.logger import setup_logging
from config.settings import (
    ANALYSIS_INTERVAL_MINUTES,
    BOT_TOKEN,
    KEEP_ALIVE,
)
from database.db import MarketBot
from handlers.onboarding import register_onboarding_handlers
from schedulers.analysis_scheduler import AnalysisScheduler
from utils.keep_alive import start_keep_alive, ping_server


logger = setup_logging()

bot_instance = None
stop_event: asyncio.Event = None

ANALYSIS_INTERVAL_SECONDS = ANALYSIS_INTERVAL_MINUTES * 60
QUICK_RETRY_SECONDS = 10 * 60


class NoContentAvailable(Exception):
    """Raised when data sources are empty and bot has nothing to post."""
    pass


async def _run_periodic_job(name: str, interval_seconds: int, job_coro):
    """Run one job forever with self-healing retries."""
    logger.info(f"{name} loop started (interval={interval_seconds}s)")
    loop = asyncio.get_running_loop()
    next_run = loop.time() + interval_seconds

    while not stop_event.is_set():
        wait_seconds = max(0.0, next_run - loop.time())

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
            break
        except asyncio.TimeoutError:
            pass

        if stop_event.is_set():
            break

        try:
            logger.info(f"{name}: execution started")
            await job_coro(bot_instance)
            logger.info(f"{name}: execution completed")
            next_run = loop.time() + interval_seconds
        except NoContentAvailable:
            logger.warning(f"{name}: no content available, quick retry in {QUICK_RETRY_SECONDS}s")
            next_run = loop.time() + QUICK_RETRY_SECONDS
        except Exception as e:
            logger.error(f"{name}: execution failed: {e}")
            next_run = loop.time() + min(300, interval_seconds)

    logger.info(f"{name} loop stopped")


async def startup_post(bot_instance):
    """Post one stock snapshot to the configured target channel and any active owner channels after startup."""
    destinations = AnalysisScheduler._resolve_destinations(bot_instance)

    if not destinations:
        logger.info("No broadcast destinations configured at startup. Waiting for /start onboarding.")
        return

    logger.info("Running startup stock snapshot for %d broadcast destinations.", len(destinations))
    try:
        await AnalysisScheduler.broadcast_analysis(bot_instance, chat_list=destinations)
    except Exception as e:
        logger.error(f"Startup stock snapshot failed: {e}")


async def setup_bot():
    """Initialize bot, database, and Telegram handlers."""
    global bot_instance

    try:
        logger.info("Initializing Market Bot...")

        bot_instance = MarketBot()
        logger.info("Database initialized")

        application = Application.builder().token(BOT_TOKEN).build()

        application.bot_data["bot_instance"] = bot_instance
        application.bot_data["bot"] = application.bot
        bot_instance.bot = application.bot
        register_onboarding_handlers(application)

        logger.info("Stock snapshot broadcast interval: %d minutes", ANALYSIS_INTERVAL_MINUTES)

        return application

    except Exception as e:
        logger.error(f"Failed to initialize bot: {e}")
        raise


async def _ensure_periodic_jobs_alive():
    """Monitor and restart periodic jobs if they crash."""
    logger.info("Starting periodic job monitor...")

    async def monitor_and_restart(name: str, interval: int, job_coro):
        """Keep a periodic job alive by restarting if it crashes."""
        while not stop_event.is_set():
            try:
                logger.info(f"Starting {name} job...")
                await _run_periodic_job(name, interval, job_coro)
            except Exception as e:
                logger.error(f"{name} job crashed: {e}", exc_info=True)
                if not stop_event.is_set():
                    logger.info(f"Restarting {name} job in 5 seconds...")
                    await asyncio.sleep(5)

            if stop_event.is_set():
                break

    monitor_tasks = [
        asyncio.create_task(
            monitor_and_restart("stock_snapshot_broadcast", ANALYSIS_INTERVAL_SECONDS, AnalysisScheduler.broadcast_analysis)
        ),
    ]

    return monitor_tasks


async def main():
    """Main async entry point."""
    ping_task = None
    periodic_tasks = []

    if KEEP_ALIVE:
        start_keep_alive()
        ping_task = asyncio.create_task(ping_server(port=8080, interval_seconds=240))
        logger.info("Keep-alive HTTP pings scheduled (every 4 minutes)")

    application = await setup_bot()

    logger.info("Starting bot initialization...")

    try:
        await application.initialize()
        await application.start()
        logger.info("Telegram polling disabled in this instance; scheduled broadcasts will handle posting.")

        warmup_task = asyncio.create_task(startup_post(bot_instance))

        def _log_warmup_outcome(task: asyncio.Task):
            if task.cancelled():
                logger.warning("Startup post task was cancelled")
                return
            err = task.exception()
            if err:
                logger.error(f"Startup post task failed: {err}")

        warmup_task.add_done_callback(_log_warmup_outcome)

        periodic_tasks = await _ensure_periodic_jobs_alive()

        logger.info("Bot is running; waiting for shutdown signal.")
        await stop_event.wait()

    except asyncio.CancelledError:
        logger.info("Bot stopped")
    finally:
        for task in periodic_tasks:
            if not task.done():
                task.cancel()
        if periodic_tasks:
            await asyncio.gather(*periodic_tasks, return_exceptions=True)

        if ping_task and not ping_task.done():
            ping_task.cancel()
            await asyncio.gather(ping_task, return_exceptions=True)

        if application.updater:
            await application.updater.stop()
        await application.stop()
        await application.shutdown()
        logger.info("Bot shutdown complete")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    stop_event = asyncio.Event()

    def handle_signal(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        loop.call_soon_threadsafe(stop_event.set)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        loop.run_until_complete(main())
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        loop.close()
