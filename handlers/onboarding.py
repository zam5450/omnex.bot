"""User onboarding for referral-owned channel posting."""
from html import escape
from urllib.parse import urlparse

from telegram import Update
from telegram.ext import (
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from utils.logger import logger

REFERRAL_LINK, CHANNEL_LINK = range(2)


WELCOME_TEXT = (
    "Welcome. I can post stock updates in your channel every 4 hours with your referral link attached.\n\n"
    "Send me your referral link first."
)

CHANNEL_PROMPT = (
    "Got it. Now send your channel link or @username.\n\n"
    "After that, add this bot as an admin in the channel and enable permission to post messages."
)

PENDING_TEXT = (
    "Setup saved. Now add this bot as an admin in your channel with permission to post messages.\n\n"
    "Once Telegram confirms the bot is an admin, I will activate the channel and start posting stock updates every 4 hours."
)


def _is_valid_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_valid_channel_reference(value: str) -> bool:
    text = value.strip()
    if text.startswith("@") and len(text) > 1:
        return True
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() in {"t.me", "telegram.me"}


def _can_post_to_channel(member) -> bool:
    status = getattr(member, "status", "")
    can_post = getattr(member, "can_post_messages", None)
    return status in {"administrator", "creator"} and can_post is not False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start onboarding by asking for a referral link."""
    if not update.message:
        return ConversationHandler.END
    await update.message.reply_text(WELCOME_TEXT)
    return REFERRAL_LINK


async def receive_referral_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Persist referral link in conversation data and ask for channel."""
    if not update.message or not update.message.text:
        return REFERRAL_LINK

    referral_link = update.message.text.strip()
    if not _is_valid_url(referral_link):
        await update.message.reply_text("Please send a valid referral link that starts with http:// or https://.")
        return REFERRAL_LINK

    context.user_data["referral_link"] = referral_link
    await update.message.reply_text(CHANNEL_PROMPT)
    return CHANNEL_LINK


async def receive_channel_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store the requested channel and activate immediately if admin access already exists."""
    if not update.message or not update.message.text or not update.effective_user:
        return CHANNEL_LINK

    channel_ref = update.message.text.strip()
    if not _is_valid_channel_reference(channel_ref):
        await update.message.reply_text("Please send a public channel @username or a t.me channel link.")
        return CHANNEL_LINK

    referral_link = context.user_data.get("referral_link")
    if not referral_link:
        await update.message.reply_text("Please use /start again so I can collect your referral link first.")
        return ConversationHandler.END

    bot_instance = context.application.bot_data["bot_instance"]
    user_id = update.effective_user.id
    bot_instance.save_pending_channel(user_id, referral_link, channel_ref)

    try:
        chat = await context.bot.get_chat(channel_ref)
        member = await context.bot.get_chat_member(chat.id, context.bot.id)
        if chat.type == "channel" and _can_post_to_channel(member):
            bot_instance.activate_user_channel(user_id, chat.id, chat.username, chat.title)
            await update.message.reply_text(
                f"Your channel {escape(chat.title or channel_ref)} is active. Stock updates will post every 4 hours."
            )
            return ConversationHandler.END
    except Exception as e:
        logger.info(f"Channel is not directly verifiable yet for user {user_id}: {e}")

    await update.message.reply_text(PENDING_TEXT)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel onboarding."""
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("Setup cancelled. Use /start whenever you want to set up a channel.")
    return ConversationHandler.END


async def on_bot_channel_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activate or deactivate channels when Telegram reports bot admin changes."""
    event = update.my_chat_member
    if not event or event.chat.type != "channel":
        return

    bot_instance = context.application.bot_data["bot_instance"]
    chat = event.chat
    new_member = event.new_chat_member

    if _can_post_to_channel(new_member):
        pending = bot_instance.get_pending_channel_for_link(chat.username, chat.title)
        if not pending:
            logger.warning(
                "Bot became admin in channel %s (%s), but no pending user setup matched it.",
                chat.id,
                chat.username or chat.title,
            )
            return

        user_id = pending["user_id"]
        bot_instance.activate_user_channel(user_id, chat.id, chat.username, chat.title)
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"Your channel {escape(chat.title or chat.username or str(chat.id))} is active. "
                    "I will post stock updates there every 4 hours with your referral link."
                ),
            )
        except Exception as e:
            logger.warning(f"Could not notify user {user_id} about activation: {e}")
        return

    status = getattr(new_member, "status", "")
    if status in {"left", "kicked", "member", "restricted"}:
        bot_instance.deactivate_channel(chat.id)
        logger.info("Deactivated channel %s after bot access changed to %s", chat.id, status)


def register_onboarding_handlers(application):
    """Register /start onboarding and bot channel-admin tracking."""
    conversation = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            REFERRAL_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_referral_link)],
            CHANNEL_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel_link)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conversation)
    application.add_handler(ChatMemberHandler(on_bot_channel_status, ChatMemberHandler.MY_CHAT_MEMBER))
