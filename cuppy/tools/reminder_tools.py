"""Reminders: Cuppy stores them and the scheduler delivers them on time."""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import ToolContext, ToolError, tool

REPEATS = ["none", "daily", "weekdays", "weekly", "monthly"]
CHANNELS = ["default", "telegram", "discord", "whatsapp", "call"]


def parse_local_datetime(value: str, tz: ZoneInfo) -> datetime:
    """Parse an ISO 8601 string. Times without an offset are taken as the owner's local time."""
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ToolError(f"Couldn't read the time '{value}'. Use ISO 8601, e.g. 2026-11-06T09:30.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def next_occurrence(due: datetime, repeat: str, tz: ZoneInfo, now: datetime | None = None) -> datetime | None:
    """Next due time for a repeating reminder, keeping the same local wall-clock time."""
    if repeat == "none":
        return None
    now = now or datetime.now(timezone.utc)
    local = due.astimezone(tz).replace(tzinfo=None)  # naive local wall-clock time
    while True:
        if repeat == "daily":
            local += timedelta(days=1)
        elif repeat == "weekly":
            local += timedelta(weeks=1)
        elif repeat == "weekdays":
            local += timedelta(days=1)
            while local.weekday() >= 5:  # skip Saturday/Sunday
                local += timedelta(days=1)
        elif repeat == "monthly":
            month = local.month % 12 + 1
            year = local.year + (local.month == 12)
            day = min(local.day, calendar.monthrange(year, month)[1])
            local = local.replace(year=year, month=month, day=day)
        else:
            return None
        candidate = local.replace(tzinfo=tz)
        if candidate > now:
            return candidate.astimezone(timezone.utc)


def fmt_local(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%a %d %b %Y, %H:%M")


@tool(
    name="create_reminder",
    description=(
        "Schedule a reminder. It's delivered at due_at through the chosen channel: a chat message "
        "(telegram/discord/whatsapp) or 'call' for a real phone call that reads the reminder aloud. "
        "Use 'default' unless the owner asks for a specific channel. Work out due_at from the "
        "current time in the latest message tag."
    ),
    properties={
        "text": {"type": "string", "description": "What to remind them about, phrased to be read back to them."},
        "due_at": {"type": "string", "description": "Local date-time in ISO 8601, e.g. 2026-11-06T09:30"},
        "repeat": {"type": "string", "enum": REPEATS},
        "channel": {"type": "string", "enum": CHANNELS},
    },
    required=["text", "due_at"],
)
async def create_reminder(
    ctx: ToolContext, text: str, due_at: str, repeat: str = "none", channel: str = "default"
) -> str:
    if repeat not in REPEATS:
        raise ToolError(f"repeat must be one of {REPEATS}")
    if channel not in CHANNELS:
        raise ToolError(f"channel must be one of {CHANNELS}")
    tz = ctx.cfg.tz
    due = parse_local_datetime(due_at, tz)
    if due < datetime.now(timezone.utc) - timedelta(minutes=1):
        raise ToolError(f"{fmt_local(due, tz)} is in the past. Pick a future time.")
    warning = ""
    unavailable = ctx.notifier.unavailable_reason(channel)
    if unavailable:
        warning = f" Warning: {unavailable} It will fall back to another channel."
    reminder_id = ctx.db.add_reminder(text.strip(), due, repeat, channel)
    repeat_txt = "" if repeat == "none" else f", repeating {repeat}"
    return f"Reminder #{reminder_id} set for {fmt_local(due, tz)} ({ctx.cfg.timezone}) via {channel}{repeat_txt}.{warning}"


@tool(name="list_reminders", description="List all upcoming (pending) reminders.")
async def list_reminders(ctx: ToolContext) -> str:
    reminders = ctx.db.pending_reminders()
    if not reminders:
        return "No upcoming reminders."
    tz = ctx.cfg.tz
    return "\n".join(
        f"#{r.id} {fmt_local(r.due_at, tz)} via {r.channel}"
        + ("" if r.repeat == "none" else f" (repeats {r.repeat})")
        + f": {r.text}"
        for r in reminders
    )


@tool(
    name="cancel_reminder",
    description="Cancel a pending reminder by id (this also stops a repeating reminder).",
    properties={"reminder_id": {"type": "integer"}},
    required=["reminder_id"],
)
async def cancel_reminder(ctx: ToolContext, reminder_id: int) -> str:
    r = ctx.db.get_reminder(int(reminder_id))
    if not r or r.status != "pending":
        raise ToolError(f"No pending reminder #{reminder_id}.")
    ctx.db.set_reminder_status(r.id, "cancelled")
    return f"Cancelled reminder #{r.id}: {r.text}"
