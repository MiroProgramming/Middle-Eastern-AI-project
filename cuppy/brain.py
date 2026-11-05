"""Cuppy's brain: talks to Claude, runs tools, keeps the conversation going.

Cost controls built in here:
- Prompt caching: the stable part of each request (personality, tools, older
  messages) is cached, so re-reading it costs ~10% of the normal price.
- Short sessions: once a conversation gets long, the cheap model summarises it
  and a fresh session starts with that summary plus your saved memories.
- Model tiers: an everyday model, a "deep" model only when you ask (/deep),
  and a cheap model for background chores.
- Usage tracking and an optional monthly budget cap (falls back to the cheap model).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import anthropic

from .config import Config
from .db import Database
from .personality import build_system_prompt
from .tools import SERVER_TOOLS, ToolContext, load_all, run_tool
from .tools.reminder_tools import fmt_local

log = logging.getLogger(__name__)

MAX_STEPS = 12  # max tool rounds per message
MAX_TOKENS = 16000

# Models that accept the server-side refusal fallback (`fallbacks: "default"`).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# USD per million tokens: (input, output, cache read). Cache writes cost 1.25x input.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
}
WEB_SEARCH_USD = 0.01


def estimate_cost(model: str, usage) -> float:
    price = next((p for name, p in PRICES.items() if model.startswith(name)), PRICES["claude-opus-5-5"])
    inp, out, read = price
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0
    return (
        (usage.input_tokens or 0) * inp
        + (usage.output_tokens or 0) * out
        + (usage.cache_read_input_tokens or 0) * read
        + (usage.cache_creation_input_tokens or 0) * inp * 1.25
    ) / 1_000_000 + searches * WEB_SEARCH_USD


def month_start(tz) -> datetime:
    return datetime.now(tz).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _dump(block) -> dict:
    """Turn a response block back into request JSON, unchanged (thinking blocks must round-trip as-is)."""
    return block.model_dump(mode="json", by_alias=True, exclude_none=True)


def _transcript(history: list[dict], limit_chars: int = 60_000) -> str:
    """Plain-text version of a conversation (text only), for summarising."""
    lines = []
    for msg in history:
        content = msg["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for block in content:
            if block.get("type") == "text" and block.get("text", "").strip():
                who = "Owner" if msg["role"] == "user" else "Cuppy"
                lines.append(f"{who}: {block['text'].strip()}")
    text = "\n".join(lines)
    return text[-limit_chars:]


class Brain:
    def __init__(self, cfg: Config, db: Database, ctx: ToolContext):
        self.cfg = cfg
        self.db = db
        self.ctx = ctx
        self.client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key or None)
        tools = load_all()
        # Sorted so the tool list is byte-identical every request (keeps the cache warm).
        self.tool_defs = [tools[name].definition() for name in sorted(tools)] + SERVER_TOOLS
        self.system = build_system_prompt(cfg)
        self.lock = asyncio.Lock()  # one owner, one conversation: handle messages in order
        self._budget_warned_month = ""

    # --- public ----------------------------------------------------------
    async def chat(self, text: str, channel: str, deep: bool = False, conversation_id: str = "main") -> str:
        async with self.lock:
            model, effort, note = self._pick_model(deep)
            history = self.db.load_conversation(conversation_id)
            try:
                reply = await self._turn(conversation_id, history, text, channel, model, effort, deep)
            except anthropic.BadRequestError as e:
                # Usually a history problem (e.g. settings changed). Start fresh and retry once.
                log.warning("Bad request, retrying with a fresh session: %s", e)
                try:
                    reply = await self._turn(
                        conversation_id, history, text, channel, model, effort, deep, force_fresh=True
                    )
                except anthropic.BadRequestError as e2:
                    log.exception("Bad request again")
                    return f"Something's wrong with my setup - the API rejected the request: {e2.message}"
            return f"{note}{reply}" if note else reply

    async def start_new_session(self, conversation_id: str = "main") -> None:
        async with self.lock:
            history = self.db.load_conversation(conversation_id)
            if history:
                await self._save_summary(conversation_id, history)
            self.db.save_conversation(conversation_id, [])

    def usage_report(self) -> str:
        tz = self.cfg.tz
        since = month_start(tz)
        rows = self.db.usage_since(since)
        if not rows:
            return "No AI usage recorded this month yet."
        total = sum(r["cost_usd"] for r in rows)
        lines = [f"Estimated AI spend since {since:%d %b}: ${total:.2f}"]
        if self.cfg.monthly_budget_usd:
            lines[0] += f" of ${self.cfg.monthly_budget_usd:.2f} budget"
        for r in rows:
            lines.append(
                f"- {r['model']}: {r['requests']} requests, ${r['cost_usd']:.2f} "
                f"(in {r['input_tokens']:,}, cached {r['cache_read_tokens']:,}, out {r['output_tokens']:,}, "
                f"searches {r['web_searches']})"
            )
        lines.append("(Estimate from list prices; your Anthropic Console shows the exact bill.)")
        return "\n".join(lines)

    # --- internals -------------------------------------------------------
    def _pick_model(self, deep: bool) -> tuple[str, str, str]:
        cfg = self.cfg
        if cfg.monthly_budget_usd:
            spent = self.db.cost_since(month_start(cfg.tz))
            if spent >= cfg.monthly_budget_usd:
                month = datetime.now(cfg.tz).strftime("%Y-%m")
                note = ""
                if self._budget_warned_month != month:
                    self._budget_warned_month = month
                    note = (
                        f"(Heads up: this month's AI budget of ${cfg.monthly_budget_usd:.2f} is used up "
                        f"(${spent:.2f}), so I've switched to my budget brain until next month.)\n\n"
                    )
                return cfg.cheap_model, "low", note
        if deep:
            return cfg.deep_model, cfg.deep_effort, ""
        return cfg.model, cfg.effort, ""

    def _tag(self, channel: str, deep: bool) -> str:
        now = datetime.now(self.cfg.tz)
        mode = " · deep mode" if deep else ""
        return f"[{now:%a %d %b %Y, %H:%M} · via {channel}{mode}]"

    def _session_context(self, summary: str | None) -> str:
        parts = ["<session_context>"]
        memories = self.db.list_memories(limit=300)
        if memories:
            parts.append("Things you remember about the owner (from long-term memory):")
            parts += [f"- #{m['id']} [{m['category']}] {m['content']}" for m in memories]
        else:
            parts.append("Long-term memory is empty so far - get to know the owner.")
        reminders = self.db.pending_reminders()[:20]
        if reminders:
            parts.append("\nUpcoming reminders:")
            parts += [f"- #{r.id} {fmt_local(r.due_at, self.cfg.tz)}: {r.text}" for r in reminders]
        if summary:
            parts.append(f"\nSummary of your previous conversation:\n{summary}")
        parts.append("</session_context>")
        return "\n".join(parts)

    async def _save_summary(self, conversation_id: str, history: list[dict]) -> str | None:
        transcript = _transcript(history)
        if not transcript:
            return None
        try:
            resp = await self.client.messages.create(
                model=self.cfg.cheap_model,
                max_tokens=2000,
                output_config={"effort": "low"},
                messages=[{
                    "role": "user",
                    "content": (
                        "Summarise this conversation between the owner and their assistant Cuppy in under "
                        "200 words, for Cuppy to read later. Keep open tasks, promises Cuppy made, decisions, "
                        "and anything emotionally important. Skip small talk.\n\n" + transcript
                    ),
                }],
            )
            self._record(self.cfg.cheap_model, resp)
            summary = "".join(b.text for b in resp.content if b.type == "text").strip()
        except anthropic.APIError as e:
            log.warning("Summary failed: %s", e)
            summary = None
        if summary:
            self.db.set(f"summary:{conversation_id}", summary)
        return summary

    async def _turn(
        self, conversation_id: str, history: list[dict], text: str, channel: str,
        model: str, effort: str, deep: bool, force_fresh: bool = False,
    ) -> str:
        new_content: list[dict] = []
        if force_fresh or not history or len(history) >= self.cfg.max_history_messages:
            summary = await self._save_summary(conversation_id, history) if history else None
            if summary is None:
                summary = self.db.get(f"summary:{conversation_id}")
            history = []
            new_content.append({"type": "text", "text": self._session_context(summary)})
        else:
            # If the last turn was cut off mid-tool-call, close it so the history stays valid.
            last = history[-1]
            if last["role"] == "assistant":
                pending = [b for b in last["content"] if isinstance(b, dict) and b.get("type") == "tool_use"]
                new_content += [
                    {"type": "tool_result", "tool_use_id": b["id"], "content": "Cancelled.", "is_error": True}
                    for b in pending
                ]
        new_content.append({"type": "text", "text": f"{self._tag(channel, deep)}\n{text}"})

        if history and history[-1]["role"] == "user":
            history[-1]["content"] = list(history[-1]["content"]) + new_content
        else:
            history.append({"role": "user", "content": new_content})

        try:
            reply = await self._run(history, model, effort)
        except anthropic.AuthenticationError:
            return "My API key isn't working - check ANTHROPIC_API_KEY in the .env file."
        except anthropic.BadRequestError:
            raise
        except anthropic.APIError as e:
            log.exception("Claude API error")
            return f"Sorry, I couldn't reach my brain just now ({type(e).__name__}). Try again in a minute?"
        self.db.save_conversation(conversation_id, history)
        return reply

    async def _create(self, history: list[dict], model: str, effort: str):
        kwargs: dict = dict(
            model=model,
            max_tokens=MAX_TOKENS,
            system=self.system,
            tools=self.tool_defs,
            messages=history,
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},  # automatic prompt caching
        )
        if model in FALLBACK_MODELS:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        resp = await self.client.beta.messages.create(**kwargs)
        self._record(model, resp)
        return resp

    def _record(self, requested_model: str, resp) -> None:
        model = getattr(resp, "model", None) or requested_model
        usage = resp.usage
        try:
            self.db.record_usage(
                model=model,
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read=usage.cache_read_input_tokens or 0,
                cache_write=usage.cache_creation_input_tokens or 0,
                web_searches=getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0,
                cost_usd=estimate_cost(model, usage),
            )
        except Exception:  # cost tracking must never break a reply
            log.exception("Couldn't record usage")

    async def _run(self, history: list[dict], model: str, effort: str) -> str:
        last_text = ""
        for step in range(MAX_STEPS):
            resp = await self._create(history, model, effort)
            history.append({"role": "assistant", "content": [_dump(b) for b in resp.content]})
            text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
            if text:
                last_text = text

            if resp.stop_reason == "refusal":
                return text or "I can't help with that one, sorry."
            if resp.stop_reason == "pause_turn":  # long web search: let it continue
                continue
            if resp.stop_reason != "tool_use":
                if resp.stop_reason == "max_tokens":
                    last_text += "\n\n(I ran out of room there - say 'continue' for the rest.)"
                return last_text or "(no reply)"

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            results = await asyncio.gather(*(run_tool(self.ctx, b.name, b.input) for b in tool_uses))
            content: list[dict] = [
                {"type": "tool_result", "tool_use_id": b.id, "content": out, "is_error": err}
                for b, (out, err) in zip(tool_uses, results)
            ]
            if step == MAX_STEPS - 2:
                content.append({"type": "text", "text": "(Step limit almost reached: wrap up now and reply to the owner.)"})
            history.append({"role": "user", "content": content})
        return last_text or "That took more steps than I'm allowed - can you break it into smaller asks?"
