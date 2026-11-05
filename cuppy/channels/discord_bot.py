"""Discord bot. Talk to Cuppy in DMs; only the owner (DISCORD_OWNER_ID) gets answers."""

from __future__ import annotations

import asyncio
import logging

import discord

from ..brain import Brain
from ..config import Config
from ..notify import DISCORD_LIMIT, Notifier, split_message
from . import handle_text

log = logging.getLogger(__name__)


async def start_discord(cfg: Config, brain: Brain, notifier: Notifier) -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.dm_messages = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        log.info("Discord bot logged in as %s", client.user)

    @client.event
    async def on_message(message: discord.Message):
        if message.author == client.user or not isinstance(message.channel, discord.DMChannel):
            return
        if cfg.discord_owner_id is None:
            await message.channel.send(
                f"Hi! I'm Cuppy. Your Discord user id is {message.author.id}.\n"
                f"Add DISCORD_OWNER_ID={message.author.id} to the .env file and restart me."
            )
            return
        if message.author.id != cfg.discord_owner_id:
            log.warning("Ignored Discord DM from stranger %s", message.author.id)
            return
        async with message.channel.typing():
            reply = await handle_text(brain, message.content, "discord")
        for chunk in split_message(reply, DISCORD_LIMIT):
            await message.channel.send(chunk)

    if cfg.discord_owner_id is not None:
        async def send_to_owner(text: str) -> None:
            user = await client.fetch_user(cfg.discord_owner_id)
            for chunk in split_message(text, DISCORD_LIMIT):
                await user.send(chunk)

        notifier.senders["discord"] = send_to_owner

    def _on_done(t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception():
            log.error("Discord bot stopped: %s", t.exception())

    task = asyncio.create_task(client.start(cfg.discord_bot_token))
    task.add_done_callback(_on_done)
    return client
