"""
Database module for the Market Data Bot.
Tracks news cache plus channel owners and referral links.
"""
import sqlite3
from typing import Optional

from utils.logger import logger
from config.settings import DB_NAME, NEWS_CACHE_HOURS


class MarketBot:
    """Database for posted news and user-owned channel configuration."""

    def __init__(self):
        self.db_name = DB_NAME
        self.bot = None
        self.init_database()

    def init_database(self):
        """Initialize SQLite database tables."""
        try:
            conn = sqlite3.connect(self.db_name, check_same_thread=False)
            c = conn.cursor()

            c.execute('''CREATE TABLE IF NOT EXISTS news_cache
                         (news_id TEXT PRIMARY KEY, title TEXT, source TEXT, posted_at TIMESTAMP, url TEXT)''')

            c.execute('''CREATE TABLE IF NOT EXISTS user_channels
                         (user_id INTEGER PRIMARY KEY,
                          referral_link TEXT NOT NULL,
                          requested_channel TEXT,
                          channel_id INTEGER,
                          channel_username TEXT,
                          channel_title TEXT,
                          status TEXT NOT NULL DEFAULT 'pending',
                          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

            conn.commit()
            conn.close()

            logger.info("Database initialized successfully")

        except Exception as e:
            logger.error(f"Database initialization failed: {e}")
            raise

    def is_news_cached(self, news_id: str) -> bool:
        """Check if news has already been posted (avoid duplicates)."""
        try:
            conn = sqlite3.connect(self.db_name, check_same_thread=False)
            c = conn.cursor()
            c.execute("""SELECT 1 FROM news_cache
                        WHERE news_id = ? AND
                        datetime(posted_at) > datetime('now', '-' || ? || ' hours')""",
                     (news_id, NEWS_CACHE_HOURS))
            result = c.fetchone()
            conn.close()
            return result is not None
        except Exception as e:
            logger.error(f"Failed to check news cache: {e}")
            return False

    def cache_news(self, news_id: str, title: str, source: str, url: str):
        """Cache posted news to avoid duplicates."""
        try:
            conn = sqlite3.connect(self.db_name, check_same_thread=False)
            c = conn.cursor()
            c.execute("""INSERT OR REPLACE INTO news_cache
                        (news_id, title, source, posted_at, url)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)""",
                     (news_id, title, source, url))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to cache news: {e}")

    def save_pending_channel(self, user_id: int, referral_link: str, requested_channel: str):
        """Store a user's referral and requested channel while admin access is pending."""
        try:
            conn = sqlite3.connect(self.db_name, check_same_thread=False)
            c = conn.cursor()
            c.execute(
                """INSERT INTO user_channels
                   (user_id, referral_link, requested_channel, status, updated_at)
                   VALUES (?, ?, ?, 'pending', CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id) DO UPDATE SET
                       referral_link = excluded.referral_link,
                       requested_channel = excluded.requested_channel,
                       channel_id = NULL,
                       channel_username = NULL,
                       channel_title = NULL,
                       status = 'pending',
                       updated_at = CURRENT_TIMESTAMP""",
                (user_id, referral_link, requested_channel),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to save pending channel for user {user_id}: {e}")

    def activate_user_channel(
        self,
        user_id: int,
        channel_id: int,
        channel_username: Optional[str],
        channel_title: Optional[str],
    ):
        """Mark a user's configured channel as active after the bot is made admin."""
        try:
            conn = sqlite3.connect(self.db_name, check_same_thread=False)
            c = conn.cursor()
            c.execute(
                """UPDATE user_channels
                   SET channel_id = ?,
                       channel_username = ?,
                       channel_title = ?,
                       status = 'active',
                       updated_at = CURRENT_TIMESTAMP
                   WHERE user_id = ?""",
                (channel_id, channel_username, channel_title, user_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to activate channel {channel_id} for user {user_id}: {e}")

    def deactivate_channel(self, channel_id: int):
        """Mark a channel inactive when the bot loses access."""
        try:
            conn = sqlite3.connect(self.db_name, check_same_thread=False)
            c = conn.cursor()
            c.execute(
                """UPDATE user_channels
                   SET status = 'inactive', updated_at = CURRENT_TIMESTAMP
                   WHERE channel_id = ?""",
                (channel_id,),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to deactivate channel {channel_id}: {e}")

    def get_pending_channel_for_link(self, channel_username: Optional[str], channel_title: Optional[str] = None):
        """Find a pending setup that matches the channel Telegram reports."""
        try:
            conn = sqlite3.connect(self.db_name, check_same_thread=False)
            c = conn.cursor()
            rows = c.execute(
                """SELECT user_id, referral_link, requested_channel
                   FROM user_channels
                   WHERE status = 'pending'
                   ORDER BY updated_at DESC"""
            ).fetchall()
            conn.close()

            normalized_username = (channel_username or "").strip().lower().lstrip("@")
            normalized_title = (channel_title or "").strip().lower()
            for user_id, referral_link, requested_channel in rows:
                requested = (requested_channel or "").strip().lower().rstrip("/")
                if normalized_username and (
                    requested == f"@{normalized_username}"
                    or requested.endswith(f"/{normalized_username}")
                    or requested.endswith(f"t.me/{normalized_username}")
                ):
                    return {
                        "user_id": user_id,
                        "referral_link": referral_link,
                        "requested_channel": requested_channel,
                    }
                if normalized_title and requested == normalized_title:
                    return {
                        "user_id": user_id,
                        "referral_link": referral_link,
                        "requested_channel": requested_channel,
                    }
            return None
        except Exception as e:
            logger.error(f"Failed to find pending channel for @{channel_username}: {e}")
            return None

    def get_active_channels(self) -> list:
        """Return active channel destinations with their owner referral links."""
        try:
            conn = sqlite3.connect(self.db_name, check_same_thread=False)
            c = conn.cursor()
            rows = c.execute(
                """SELECT channel_id, referral_link, channel_title
                   FROM user_channels
                   WHERE status = 'active' AND channel_id IS NOT NULL"""
            ).fetchall()
            conn.close()
            return [
                {
                    "chat_id": row[0],
                    "referral_link": row[1],
                    "channel_title": row[2],
                }
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Failed to load active channels: {e}")
            return []
