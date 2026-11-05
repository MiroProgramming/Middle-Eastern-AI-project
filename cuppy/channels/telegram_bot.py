"""Telegram bot. Only the owner (TELEGRAM_OWNER_ID) can talk to Cuppy."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from ..brain import Brain
from ..config import Config
from ..notify import TELEGRAM_LIMIT, Notifier, split_message
from . import handle_text

log = logging.getLogger(__name__)


async def _keep_typing(bot, chat_id: int) -> None:
    while True:
        with contextlib.suppress(Exception):
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(4)


async def start_telegram(cfg: Config, brain: Brain, notifier: Notifier) -> Application:
    app = Application.builder().token(cfg.telegram_bot_token).build()

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        if msg is None or user is None:
            return
        if cfg.telegram_owner_id is None:
            await msg.reply_text(
                f"Hi! I'm Cuppy. Your Telegram user id is {user.id}.\n"
                f"Add TELEGRAM_OWNER_ID={user.id} to the .env file and restart me."
            )
            return
        if user.id != cfg.telegram_owner_id:
            log.warning("Ignored Telegram message from stranger %s", user.id)
            return
        if not msg.text:
            await msg.reply_text("I can only read text for now - voice notes are coming in a later version.")
            return
        typing = asyncio.create_task(_keep_typing(context.bot, msg.chat_id))
        try:
            reply = await handle_text(brain, msg.text, "telegram")
        finally:
            typing.cancel()
        for chunk in split_message(reply, TELEGRAM_LIMIT):
            await msg.reply_text(chunk)

    app.add_handler(MessageHandler(filters.ALL, on_message))
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=False)

    if cfg.telegram_owner_id is not None:
        async def send_to_owner(text: str) -> None:
            for chunk in split_message(text, TELEGRAM_LIMIT):
                await app.bot.send_message(chat_id=cfg.telegram_owner_id, text=chunk)

        notifier.senders["telegram"] = send_to_owner
    log.info("Telegram bot running")
    return app
