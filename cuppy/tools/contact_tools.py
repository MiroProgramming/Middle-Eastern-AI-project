"""Reach the owner right now: chat message, WhatsApp, or a phone call."""

from __future__ import annotations

from . import ToolContext, ToolError, tool


@tool(
    name="contact_owner",
    description=(
        "Immediately send the owner a message or phone them. Only use this when they ask you to "
        "(e.g. 'call me now', 'WhatsApp me the summary'), or for something truly urgent. For "
        "anything in the future, use create_reminder instead. 'call' rings their phone and reads "
        "the message aloud."
    ),
    properties={
        "message": {"type": "string"},
        "channel": {"type": "string", "enum": ["default", "telegram", "discord", "whatsapp", "call"]},
    },
    required=["message"],
)
async def contact_owner(ctx: ToolContext, message: str, channel: str = "default") -> str:
    unavailable = ctx.notifier.unavailable_reason(channel)
    if unavailable:
        raise ToolError(unavailable)
    used = await ctx.notifier.deliver(message, channel)
    return f"Delivered via {used}."
