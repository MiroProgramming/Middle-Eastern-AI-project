"""Tool registry.

A tool is an async Python function Cuppy can call. Register one with
`@tool(...)`; it receives a `ToolContext` plus the arguments Claude chose, and
returns a string (what Claude sees as the result).

To add a new ability (e.g. your car later on), create a new file in this
folder, write a function with `@tool`, and import the module in `load_all()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from ..config import Config
    from ..db import Database
    from ..notify import Notifier


class ToolError(Exception):
    """Raise inside a tool to report a clean error message back to Claude."""


@dataclass
class ToolContext:
    cfg: "Config"
    db: "Database"
    notifier: "Notifier"


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Awaitable[str]]
    # Returns True when the tool's integration is configured. Tools that aren't
    # configured are still offered, but answer with setup instructions.
    is_enabled: Callable[["Config"], bool]
    setup_hint: str = ""

    def definition(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


REGISTRY: dict[str, Tool] = {}


def tool(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
    enabled: Callable[["Config"], bool] = lambda cfg: True,
    setup_hint: str = "",
):
    def decorator(fn: Callable[..., Awaitable[str]]):
        REGISTRY[name] = Tool(
            name=name,
            description=description,
            input_schema={
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
            handler=fn,
            is_enabled=enabled,
            setup_hint=setup_hint,
        )
        return fn

    return decorator


def load_all() -> dict[str, Tool]:
    # Importing the modules runs their @tool decorators.
    from . import contact_tools, google_tools, memory_tools, moodle_tools, reminder_tools  # noqa: F401

    return REGISTRY


# Anthropic-hosted tools: these run on Anthropic's servers, no code needed here.
SERVER_TOOLS: list[dict] = [
    # Each search costs ~$0.01 plus the tokens of the results, so keep it capped.
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 3},
]


async def run_tool(ctx: ToolContext, name: str, args: dict) -> tuple[str, bool]:
    """Run a tool. Returns (result_text, is_error)."""
    t = REGISTRY.get(name)
    if t is None:
        return f"Unknown tool: {name}", True
    if not t.is_enabled(ctx.cfg):
        return f"The '{name}' tool isn't set up yet. {t.setup_hint}".strip(), True
    if not isinstance(args, dict):
        return "Tool input must be a JSON object.", True
    missing = [k for k in t.input_schema.get("required", []) if k not in args]
    if missing:
        return f"Missing required argument(s): {', '.join(missing)}", True
    allowed = set(t.input_schema.get("properties", {}))
    clean = {k: v for k, v in args.items() if k in allowed}
    try:
        return await t.handler(ctx, **clean), False
    except ToolError as e:
        return str(e), True
    except Exception as e:  # never let one broken tool crash the conversation
        return f"Tool '{name}' failed: {type(e).__name__}: {e}", True
