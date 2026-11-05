"""Delivers messages and phone calls to the owner on whichever channel is wanted."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Awaitable, Callable
from xml.sax.saxutils import escape

from .config import Config

if TYPE_CHECKING:
    from twilio.rest import Client as TwilioClient

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4000
DISCORD_LIMIT = 1900
WHATSAPP_LIMIT = 1500


def split_message(text: str, limit: int) -> list[str]:
    """Split long text into chunks under a chat app's length limit, preferring line breaks."""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks or [""]


class Notifier:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        # Chat apps register a "send to owner" function here when they start.
        self.senders: dict[str, Callable[[str], Awaitable[None]]] = {}
        self._twilio: "TwilioClient | None" = None

    # --- Twilio ----------------------------------------------------------
    @property
    def twilio(self) -> "TwilioClient":
        if self._twilio is None:
            from twilio.rest import Client

            self._twilio = Client(self.cfg.twilio_account_sid, self.cfg.twilio_auth_token)
        return self._twilio

    async def send_whatsapp(self, text: str) -> None:
        for chunk in split_message(text, WHATSAPP_LIMIT):
            await asyncio.to_thread(
                self.twilio.messages.create,
                from_=f"whatsapp:{self.cfg.twilio_whatsapp_number}",
                to=f"whatsapp:{self.cfg.owner_whatsapp_number}",
                body=chunk,
            )

    async def call(self, text: str) -> None:
        """Ring the owner's phone and read the message aloud (said twice, in case they miss it)."""
        voice = escape(self.cfg.twilio_voice, {'"': "&quot;"})
        said = escape(text)
        twiml = (
            f'<Response><Pause length="1"/><Say voice="{voice}">Hey {escape(self.cfg.owner_name)}, it\'s Cuppy. {said}</Say>'
            f'<Pause length="1"/><Say voice="{voice}">Again: {said}</Say></Response>'
        )
        await asyncio.to_thread(
            self.twilio.calls.create,
            to=self.cfg.owner_phone_number,
            from_=self.cfg.twilio_phone_number,
            twiml=twiml,
        )

    # --- routing ---------------------------------------------------------
    def resolve(self, channel: str) -> str:
        return self.cfg.default_notify_channel if channel in ("", "default") else channel

    def available(self) -> list[str]:
        chans = list(self.senders)
        if self.cfg.whatsapp_enabled:
            chans.append("whatsapp")
        if self.cfg.calls_enabled:
            chans.append("call")
        return chans

    def unavailable_reason(self, channel: str) -> str | None:
        ch = self.resolve(channel)
        if ch == "call" and not self.cfg.calls_enabled:
            return "Phone calls aren't set up (needs Twilio + TWILIO_PHONE_NUMBER + OWNER_PHONE_NUMBER)."
        if ch == "whatsapp" and not self.cfg.whatsapp_enabled:
            return "WhatsApp isn't set up (needs Twilio + TWILIO_WHATSAPP_NUMBER)."
        if ch in ("telegram", "discord") and ch not in self.senders:
            return f"{ch.title()} isn't connected (check the bot token and owner id in .env)."
        if ch not in ("telegram", "discord", "whatsapp", "call"):
            return f"Unknown channel '{ch}'."
        return None

    async def _send_on(self, channel: str, text: str) -> None:
        if channel == "call":
            await self.call(text)
        elif channel == "whatsapp":
            await self.send_whatsapp(text)
        elif channel in self.senders:
            await self.senders[channel](text)
        else:
            raise RuntimeError(self.unavailable_reason(channel) or f"Can't send on {channel}")

    async def deliver(self, text: str, channel: str = "default", call_text: str | None = None) -> str:
        """Deliver on the requested channel, falling back to any other working channel.

        Returns the channel actually used. For calls, a written copy is also sent to chat.
        """
        wanted = self.resolve(channel)
        order = [wanted] + [c for c in self.available() if c not in (wanted, "call")]
        errors = []
        for ch in order:
            if self.unavailable_reason(ch):
                continue
            try:
                if ch == "call":
                    await self.call(call_text or text)
                    # Also leave a written copy in chat, if any chat app is up.
                    for chat in [c for c in self.available() if c != "call"]:
                        try:
                            await self._send_on(chat, text)
                            break
                        except Exception as e:  # pragma: no cover - best effort
                            log.warning("Written copy on %s failed: %s", chat, e)
                else:
                    await self._send_on(ch, text)
                return ch
            except Exception as e:
                log.exception("Delivery via %s failed", ch)
                errors.append(f"{ch}: {e}")
        raise RuntimeError("Couldn't reach the owner on any channel. " + "; ".join(errors))
