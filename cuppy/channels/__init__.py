"""Chat apps Cuppy listens on. All of them share the same commands and the same brain."""

from __future__ import annotations

import logging

from ..brain import Brain

log = logging.getLogger(__name__)

HELP = """Commands:
/deep <question> - use my smartest (pricier) brain for one hard question
/new - start a fresh conversation (I keep a summary + my memories)
/usage - this month's estimated AI cost
/help - this message
Anything else: just talk to me."""


async def handle_text(brain: Brain, text: str, channel: str) -> str:
    """Shared message handling for every chat app."""
    text = (text or "").strip()
    if not text:
        return "I only understand text messages for now."
    cmd, _, rest = text.partition(" ")
    cmd = cmd.lower().split("@")[0]  # Telegram adds @botname in groups
    try:
        if cmd in ("/start", "/help"):
            return "Hey, it's Cuppy 🚗☕\n\n" + HELP
        if cmd == "/usage":
            return brain.usage_report()
        if cmd == "/new":
            await brain.start_new_session()
            return "Fresh start! I've kept a summary of our last chat and everything I remember about you."
        if cmd == "/deep":
            if not rest.strip():
                return "Usage: /deep <your question>"
            return await brain.chat(rest.strip(), channel, deep=True)
        return await brain.chat(text, channel)
    except Exception as e:
        log.exception("Error handling message")
        return f"Oops, something broke on my side ({type(e).__name__}). Check my logs."
