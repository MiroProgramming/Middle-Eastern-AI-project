"""Offline tests: no API key or network needed (Claude is faked)."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from anthropic.types.beta import BetaTextBlock, BetaToolUseBlock, BetaUsage

from cuppy.brain import Brain, estimate_cost
from cuppy.channels import handle_text
from cuppy.config import Config
from cuppy.db import Database
from cuppy.notify import Notifier, split_message
from cuppy.tools import ToolContext, load_all, run_tool
from cuppy.tools.reminder_tools import next_occurrence


def usage(**kw):
    base = dict(input_tokens=100, output_tokens=50, cache_read_input_tokens=1000, cache_creation_input_tokens=0)
    base.update(kw)
    return BetaUsage(**base)


def response(blocks, stop_reason, model="claude-sonnet-5-5"):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason, usage=usage(), model=model)


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def create(self, **kwargs):
        # Snapshot the request so later mutation of history doesn't affect assertions.
        self.calls.append(json.loads(json.dumps(kwargs, default=str)))
        return self.script.pop(0)


def make_brain(tmp_path, script, cheap_script=(), **cfg_kw):
    cfg = Config(anthropic_api_key="test", timezone="Africa/Cairo", data_dir=tmp_path, owner_name="Marwan", **cfg_kw)
    db = Database(tmp_path / "t.db")
    notifier = Notifier(cfg)
    brain = Brain(cfg, db, ToolContext(cfg=cfg, db=db, notifier=notifier))
    fake = FakeMessages(script)
    cheap = FakeMessages(cheap_script)
    brain.client = SimpleNamespace(beta=SimpleNamespace(messages=fake), messages=cheap)
    return brain, fake, cheap, db, notifier


def test_tool_definitions_are_valid():
    tools = load_all()
    assert {"remember", "create_reminder", "gmail_search", "moodle_deadlines", "contact_owner"} <= set(tools)
    for t in tools.values():
        d = t.definition()
        assert d["input_schema"]["type"] == "object"
        assert set(d["input_schema"]["required"]) <= set(d["input_schema"]["properties"])
        assert len(d["description"]) > 10


def test_chat_with_tool_call_creates_reminder_and_persists(tmp_path):
    due = (datetime.now(ZoneInfo("Africa/Cairo")) + timedelta(days=1)).strftime("%Y-%m-%dT09:00")
    script = [
        response(
            [
                BetaTextBlock(type="text", text="On it."),
                BetaToolUseBlock(
                    type="tool_use", id="tu_1", name="create_reminder",
                    input={"text": "Study renal physiology", "due_at": due},
                ),
            ],
            "tool_use",
        ),
        response([BetaTextBlock(type="text", text="Done! I'll remind you at 9.")], "end_turn"),
    ]
    brain, fake, _, db, _ = make_brain(tmp_path, script)
    reply = asyncio.run(brain.chat("remind me tomorrow at 9 to study renal", "telegram"))
    assert reply == "Done! I'll remind you at 9."

    # Reminder stored in UTC, 09:00 Cairo time.
    [r] = db.pending_reminders()
    assert r.text == "Study renal physiology"
    assert r.due_at.astimezone(ZoneInfo("Africa/Cairo")).hour == 9

    # Request shape: cheap everyday model, caching, fallbacks, sorted tools + web search.
    first = fake.calls[0]
    assert first["model"] == "claude-sonnet-5-5"
    assert first["cache_control"] == {"type": "ephemeral"}
    assert first["fallbacks"] == "default"
    assert "temperature" not in first
    assert first["tools"][-1]["type"].startswith("web_search")
    # First message of a fresh session carries memory context + time tag.
    first_user = first["messages"][0]["content"]
    assert "<session_context>" in first_user[0]["text"]
    assert "via telegram" in first_user[1]["text"]

    # Second call includes the tool result for tu_1.
    second_msgs = fake.calls[1]["messages"]
    assert second_msgs[-1]["content"][0]["type"] == "tool_result"
    assert second_msgs[-1]["content"][0]["tool_use_id"] == "tu_1"
    assert "Reminder #1 set" in second_msgs[-1]["content"][0]["content"]

    # History saved: user, assistant(tool_use), user(tool_result), assistant.
    history = db.load_conversation("main")
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    # Usage tracked for both calls.
    assert "claude-sonnet-5-5: 2 requests" in brain.usage_report()


def test_deep_mode_uses_deep_model(tmp_path):
    brain, fake, *_ = make_brain(tmp_path, [response([BetaTextBlock(type="text", text="Deep answer")], "end_turn")])
    reply = asyncio.run(handle_text(brain, "/deep explain the RAAS cascade", "discord"))
    assert reply == "Deep answer"
    assert fake.calls[0]["model"] == "claude-opus-5-5"
    assert fake.calls[0]["output_config"] == {"effort": "high"}


def test_budget_cap_switches_to_cheap_model(tmp_path):
    brain, fake, _, db, _ = make_brain(
        tmp_path, [response([BetaTextBlock(type="text", text="hi")], "end_turn", model="claude-haiku-5-5")],
        monthly_budget_usd=1.0,
    )
    db.record_usage("claude-sonnet-5-5", 0, 0, 0, 0, 0, cost_usd=5.0)
    reply = asyncio.run(brain.chat("hello", "telegram"))
    assert fake.calls[0]["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in fake.calls[0]  # haiku has no server-side fallback
    assert "budget" in reply and reply.endswith("hi")


def test_long_history_rolls_over_with_summary(tmp_path):
    script = [response([BetaTextBlock(type="text", text=f"r{i}")], "end_turn") for i in range(3)]
    summary = SimpleNamespace(content=[BetaTextBlock(type="text", text="They talked about exams.")],
                              usage=usage(), model="claude-haiku-5-5")
    brain, fake, cheap, db, _ = make_brain(tmp_path, script, cheap_script=[summary], max_history_messages=4)
    for msg in ["a", "b", "c"]:
        asyncio.run(brain.chat(msg, "telegram"))
    # Third message started a fresh session seeded with the summary.
    assert len(cheap.calls) == 1 and cheap.calls[0]["model"] == "claude-haiku-5-5"
    third_first_msg = fake.calls[2]["messages"][0]["content"][0]["text"]
    assert "They talked about exams." in third_first_msg
    assert len(db.load_conversation("main")) == 2


def test_dangling_tool_call_is_closed(tmp_path):
    brain, fake, _, db, _ = make_brain(tmp_path, [response([BetaTextBlock(type="text", text="ok")], "end_turn")])
    db.save_conversation("main", [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_x", "name": "list_reminders", "input": {}}]},
    ])
    asyncio.run(brain.chat("still there?", "telegram"))
    last_user = fake.calls[0]["messages"][-1]["content"]
    assert last_user[0]["type"] == "tool_result" and last_user[0]["tool_use_id"] == "tu_x"
    assert "still there?" in last_user[-1]["text"]


def test_unconfigured_integrations_explain_setup(tmp_path):
    cfg = Config(timezone="UTC", data_dir=tmp_path)
    db = Database(tmp_path / "t.db")
    ctx = ToolContext(cfg=cfg, db=db, notifier=Notifier(cfg))
    load_all()
    out, err = asyncio.run(run_tool(ctx, "gmail_search", {"query": "is:unread"}))
    assert err and "google_auth.py" in out
    out, err = asyncio.run(run_tool(ctx, "moodle_courses", {}))
    assert err and "MOODLE_TOKEN" in out
    out, err = asyncio.run(run_tool(ctx, "contact_owner", {"message": "hi", "channel": "call"}))
    assert err and "Phone calls aren't set up" in out


def test_moodle_file_refuses_other_hosts(tmp_path):
    cfg = Config(timezone="UTC", data_dir=tmp_path, moodle_url="https://moodle.uni.edu", moodle_token="secret")
    ctx = ToolContext(cfg=cfg, db=Database(tmp_path / "t.db"), notifier=Notifier(cfg))
    load_all()
    out, err = asyncio.run(run_tool(ctx, "moodle_read_file", {"file_url": "https://evil.example.com/x.pdf"}))
    assert err and "isn't on the owner's Moodle" in out


def test_memory_tools(tmp_path):
    cfg = Config(timezone="UTC", data_dir=tmp_path)
    db = Database(tmp_path / "t.db")
    ctx = ToolContext(cfg=cfg, db=db, notifier=Notifier(cfg))
    load_all()
    asyncio.run(run_tool(ctx, "remember", {"fact": "Struggles with acid-base disorders", "category": "study"}))
    out, _ = asyncio.run(run_tool(ctx, "recall", {"query": "acid-base"}))
    assert "acid-base" in out and "[study]" in out
    out, err = asyncio.run(run_tool(ctx, "forget", {"memory_id": 1}))
    assert not err and db.list_memories() == []


def test_reminder_rejects_past_time(tmp_path):
    cfg = Config(timezone="UTC", data_dir=tmp_path)
    ctx = ToolContext(cfg=cfg, db=Database(tmp_path / "t.db"), notifier=Notifier(cfg))
    load_all()
    out, err = asyncio.run(run_tool(ctx, "create_reminder", {"text": "x", "due_at": "2020-01-01T09:00"}))
    assert err and "in the past" in out


@pytest.mark.parametrize(
    "repeat,expected_local",
    [
        ("daily", datetime(2026, 11, 6, 8, 0)),
        ("weekly", datetime(2026, 11, 12, 8, 0)),
        ("weekdays", datetime(2026, 11, 9, 8, 0)),  # Fri 6 Nov -> Mon 9 Nov
        ("monthly", datetime(2026, 12, 5, 8, 0)),
    ],
)
def test_next_occurrence(repeat, expected_local):
    tz = ZoneInfo("Africa/Cairo")
    start = datetime(2026, 11, 5, 8, 0, tzinfo=tz)  # Thursday
    if repeat == "weekdays":
        start = datetime(2026, 11, 6, 8, 0, tzinfo=tz)  # Friday
    now = start.astimezone(timezone.utc) + timedelta(minutes=1)
    nxt = next_occurrence(start, repeat, tz, now=now)
    assert nxt.astimezone(tz).replace(tzinfo=None) == expected_local


def test_reminder_delivery_falls_back(tmp_path):
    cfg = Config(timezone="UTC", data_dir=tmp_path, default_notify_channel="telegram")
    notifier = Notifier(cfg)
    sent = []

    async def discord_send(text):
        sent.append(text)

    notifier.senders["discord"] = discord_send  # telegram not connected -> falls back to discord
    used = asyncio.run(notifier.deliver("hello", "default"))
    assert used == "discord" and sent == ["hello"]


def test_split_message_and_cost():
    parts = split_message("line\n" * 2000, 100)
    assert all(len(p) <= 100 for p in parts)
    cost = estimate_cost("claude-sonnet-5-5", usage(input_tokens=1_000_000, output_tokens=0, cache_read_input_tokens=0))
    assert cost == pytest.approx(2.0)
