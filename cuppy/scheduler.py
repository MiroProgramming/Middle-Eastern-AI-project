"""Background jobs: delivers due reminders and the daily morning briefing."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from .brain import Brain
from .config import Config
from .db import Database
from .notify import Notifier
from .tools.reminder_tools import next_occurrence

log = logging.getLogger(__name__)

TICK_SECONDS = 20
MAX_ATTEMPTS = 3

BRIEFING_PROMPT = (
    "Morning briefing time. Check what's relevant with your tools (today's calendar, unread "
    "important email from the last day, Moodle deadlines in the next 7 days, today's reminders), "
    "then write a short, warm morning message: the day ahead, anything urgent, and one line of "
    "encouragement. Skip any source that isn't connected without complaining about it. Keep it "
    "under 150 words."
)


class Scheduler:
    def __init__(self, cfg: Config, db: Database, notifier: Notifier, brain: Brain):
        self.cfg = cfg
        self.db = db
        self.notifier = notifier
        self.brain = brain

    async def run_forever(self) -> None:
        log.info("Scheduler started")
        while True:
            try:
                await self.deliver_due_reminders()
                await self.maybe_send_briefing()
            except Exception:
                log.exception("Scheduler tick failed")
            await asyncio.sleep(TICK_SECONDS)

    async def deliver_due_reminders(self) -> None:
        for r in self.db.due_reminders():
            message = f"⏰ Reminder: {r.text}"
            try:
                used = await self.notifier.deliver(message, r.channel, call_text=f"This is your reminder. {r.text}")
                log.info("Reminder #%s delivered via %s", r.id, used)
            except Exception:
                attempts = self.db.bump_attempts(r.id)
                log.exception("Reminder #%s failed (attempt %s)", r.id, attempts)
                if attempts >= MAX_ATTEMPTS:
                    self.db.set_reminder_status(r.id, "failed")
                continue
            nxt = next_occurrence(r.due_at, r.repeat, self.cfg.tz)
            if nxt:
                self.db.reschedule_reminder(r.id, nxt)
            else:
                self.db.set_reminder_status(r.id, "done")

    async def maybe_send_briefing(self) -> None:
        if not self.cfg.briefing_time:
            return
        now = datetime.now(self.cfg.tz)
        hour, minute = (int(x) for x in self.cfg.briefing_time.split(":"))
        today = now.date().isoformat()
        if (now.hour, now.minute) < (hour, minute) or self.db.get("last_briefing") == today:
            return
        # Only send if we're within 2 hours of the briefing time (avoid a stale one after downtime).
        if (now.hour * 60 + now.minute) - (hour * 60 + minute) > 120:
            self.db.set("last_briefing", today)
            return
        self.db.set("last_briefing", today)
        text = await self.brain.chat(BRIEFING_PROMPT, channel="scheduler")
        await self.notifier.deliver(text, "default")
