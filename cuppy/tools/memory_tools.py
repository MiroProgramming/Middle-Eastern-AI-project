"""Long-term memory: facts Cuppy keeps about you."""

from __future__ import annotations

from . import ToolContext, ToolError, tool

CATEGORIES = ["general", "people", "preferences", "study", "health", "projects", "schedule", "car"]


@tool(
    name="remember",
    description=(
        "Save a lasting fact about the owner to long-term memory (preferences, people, goals, "
        "exam dates, routines, topics they struggle with). Write it as a standalone sentence. "
        "Don't save trivial or temporary things, and don't duplicate an existing memory."
    ),
    properties={
        "fact": {"type": "string", "description": "The fact, e.g. 'Has a cardiology exam on 2026-12-14.'"},
        "category": {"type": "string", "enum": CATEGORIES},
    },
    required=["fact"],
)
async def remember(ctx: ToolContext, fact: str, category: str = "general") -> str:
    if category not in CATEGORIES:
        category = "general"
    memory_id = ctx.db.add_memory(fact.strip(), category)
    return f"Saved memory #{memory_id} ({category})."


@tool(
    name="recall",
    description="Search long-term memory by keywords. Returns matching memories with their ids.",
    properties={"query": {"type": "string", "description": "Keywords to search for."}},
    required=["query"],
)
async def recall(ctx: ToolContext, query: str) -> str:
    rows = ctx.db.search_memories(query)
    if not rows:
        return "No matching memories."
    return "\n".join(f"#{r['id']} [{r['category']}] {r['content']}" for r in rows)


@tool(
    name="list_memories",
    description="List everything in long-term memory, optionally only one category.",
    properties={"category": {"type": "string", "enum": CATEGORIES}},
)
async def list_memories(ctx: ToolContext, category: str | None = None) -> str:
    rows = ctx.db.list_memories(category)
    if not rows:
        return "Memory is empty."
    return "\n".join(f"#{r['id']} [{r['category']}] {r['content']}" for r in rows)


@tool(
    name="forget",
    description="Delete a memory by id (use when a fact is wrong or outdated, or the owner asks).",
    properties={"memory_id": {"type": "integer"}},
    required=["memory_id"],
)
async def forget(ctx: ToolContext, memory_id: int) -> str:
    if not ctx.db.delete_memory(int(memory_id)):
        raise ToolError(f"No memory #{memory_id}.")
    return f"Deleted memory #{memory_id}."
