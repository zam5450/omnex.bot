"""
Logging configuration for the Market Data Bot
"""
import logging
import sys


def setup_logging():
    """Configure logging for production and Windows-friendly output."""
    logger = logging.getLogger("market_bot")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        logger.handlers.clear()

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    file_handler = logging.FileHandler('market_bot.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream = sys.stdout
    if hasattr(stream, 'reconfigure'):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    stream_handler = logging.StreamHandler(stream)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # Avoid leaking bot token via verbose HTTP request logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    return logger


logger = setup_logging()
