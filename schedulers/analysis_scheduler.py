"""
Analysis broadcasting scheduler.
"""
import asyncio
from config.settings import POST_MIN_SECONDS_BETWEEN_MESSAGES, TARGET_CHANNEL_ID
from services.posting import PostingService
from utils.logger import logger
from services.analysis import AnalysisService


class AnalysisScheduler:
    """Handle scheduled stock snapshot broadcasting."""

    @staticmethod
    def _normalize_destination(destination):
        """Return chat_id and referral link from legacy tuples or channel dicts."""
        if isinstance(destination, dict):
            return destination.get("chat_id"), destination.get("referral_link", "")
        if isinstance(destination, (list, tuple)):
            return destination[0], destination[2] if len(destination) > 2 else ""
        return destination, ""

    @staticmethod
    def _resolve_destinations(bot_instance, chat_list: list = None) -> list:
        """Resolve active user channels, with legacy TARGET_CHANNEL_ID as fallback."""
        if chat_list is not None:
            return chat_list

        active_channels = bot_instance.get_active_channels()
        if active_channels:
            return active_channels

        if TARGET_CHANNEL_ID:
            try:
                chat_id = int(TARGET_CHANNEL_ID)
                logger.info(f"Legacy target channel resolved: {chat_id}")
                return [{"chat_id": chat_id, "referral_link": ""}]
            except ValueError:
                logger.error(f"Invalid TARGET_CHANNEL_ID: {TARGET_CHANNEL_ID}")
                return []

        logger.warning("No active user channels and TARGET_CHANNEL_ID is not set")
        return []

    @staticmethod
    async def broadcast_analysis(bot_instance, chat_list: list = None):
        """Broadcast stock performance snapshots plus each channel owner's ad."""
        try:
            logger.info("Starting stock snapshot broadcast...")

            destinations = AnalysisScheduler._resolve_destinations(bot_instance, chat_list)
            if not destinations:
                return

            stock_prices = await AnalysisService.fetch_top_stock_prices()

            successful = 0
            for destination in destinations:
                cid, referral_link = AnalysisScheduler._normalize_destination(destination)
                if not cid:
                    continue

                snapshot_message = PostingService.format_market_snapshot_message(
                    stock_prices,
                    referral_link=referral_link,
                )
                try:
                    await bot_instance.bot.send_message(
                        chat_id=cid,
                        text=snapshot_message,
                        parse_mode='HTML',
                    )
                    successful += 1
                except Exception as e:
                    logger.error(f"Failed to send stock snapshot to chat {cid}: {e}")

                await asyncio.sleep(POST_MIN_SECONDS_BETWEEN_MESSAGES)

            logger.info("Stock snapshot broadcast complete: %d/%d channels", successful, len(destinations))

        except Exception as e:
            logger.error(f"Analysis broadcast failed: {e}")
            try:
                destinations = AnalysisScheduler._resolve_destinations(bot_instance, chat_list)
                if destinations:
                    cid, referral_link = AnalysisScheduler._normalize_destination(destinations[0])
                    fallback = PostingService.format_market_snapshot_message([], referral_link=referral_link)
                    await bot_instance.bot.send_message(
                        chat_id=cid,
                        text=fallback,
                        parse_mode='HTML',
                    )
                    logger.info("Analysis fallback message posted.")
            except Exception as last_err:
                logger.error(f"Analysis fallback also failed: {last_err}")
                try:
                    from bot import NoContentAvailable
                    raise NoContentAvailable("Analysis posting failed completely")
                except ImportError:
                    pass

    @staticmethod
    async def send_analysis_to_chat(bot_instance, chat_id: int, market: str = 'stocks'):
        """Send current analysis to a specific chat."""
        try:
            logger.info(f"Fetching {market} analysis for chat {chat_id}...")

            if market.lower() == 'stocks':
                stock_prices = await AnalysisService.fetch_top_stock_prices()
                message = PostingService.format_market_snapshot_message(stock_prices)
            else:
                message = "Unknown market type. Use 'stocks'."

            await bot_instance.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode='HTML',
            )

            logger.info(f"{market.title()} analysis sent to chat {chat_id}")

        except Exception as e:
            logger.error(f"Failed to send {market} analysis to {chat_id}: {e}")
            raise
