"""Moodle: courses, deadlines and lecture files.

Uses Moodle's web-service API, the same one the official Moodle mobile app
uses. Get a token with `python scripts/moodle_token.py`.
"""

from __future__ import annotations

import io
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from . import ToolContext, ToolError, tool

SETUP = "Set MOODLE_URL and MOODLE_TOKEN in .env (run `python scripts/moodle_token.py` to get a token)."
MAX_FILE_CHARS = 60_000


def _enabled(cfg) -> bool:
    return cfg.moodle_enabled


async def _call(cfg, function: str, **params):
    query = {"wstoken": cfg.moodle_token, "wsfunction": function, "moodlewsrestformat": "json"}
    for key, value in params.items():
        if isinstance(value, list):
            for i, v in enumerate(value):
                query[f"{key}[{i}]"] = v
        else:
            query[key] = value
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{cfg.moodle_url}/webservice/rest/server.php", data=query)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("exception"):
        raise ToolError(f"Moodle error ({function}): {data.get('message', data)}")
    return data


async def _user_id(cfg) -> int:
    info = await _call(cfg, "core_webservice_get_site_info")
    return info["userid"]


def _when(ts: int | None, tz) -> str:
    if not ts:
        return "no date"
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(tz).strftime("%a %d %b %Y, %H:%M")


@tool(name="moodle_courses", description="List the owner's enrolled Moodle courses with their ids.",
      enabled=_enabled, setup_hint=SETUP)
async def moodle_courses(ctx: ToolContext) -> str:
    courses = await _call(ctx.cfg, "core_enrol_get_users_courses", userid=await _user_id(ctx.cfg))
    if not courses:
        return "No courses found."
    return "\n".join(f"id={c['id']}: {c.get('fullname', c.get('shortname'))}" for c in courses)


@tool(
    name="moodle_deadlines",
    description="Upcoming Moodle deadlines and events (assignments, quizzes, etc.) for the next N days.",
    properties={"days_ahead": {"type": "integer", "description": "Default 14"}},
    enabled=_enabled,
    setup_hint=SETUP,
)
async def moodle_deadlines(ctx: ToolContext, days_ahead: int = 14) -> str:
    now = int(time.time())
    data = await _call(
        ctx.cfg,
        "core_calendar_get_action_events_by_timesort",
        timesortfrom=now,
        timesortto=now + max(1, int(days_ahead)) * 86400,
        limitnum=50,
    )
    events = data.get("events", [])
    if not events:
        return f"No Moodle deadlines in the next {days_ahead} days."
    lines = []
    for e in events:
        course = (e.get("course") or {}).get("fullname", "")
        lines.append(f"{_when(e.get('timesort'), ctx.cfg.tz)} | {course} | {e.get('name')}")
    return "\n".join(lines)


@tool(
    name="moodle_course_contents",
    description="List the sections, activities and files of a Moodle course (use moodle_courses for ids). Files include a file_url for moodle_read_file.",
    properties={"course_id": {"type": "integer"}},
    required=["course_id"],
    enabled=_enabled,
    setup_hint=SETUP,
)
async def moodle_course_contents(ctx: ToolContext, course_id: int) -> str:
    sections = await _call(ctx.cfg, "core_course_get_contents", courseid=int(course_id))
    lines = []
    for s in sections:
        lines.append(f"## {s.get('name', 'Section')}")
        for m in s.get("modules", []):
            lines.append(f"- [{m.get('modname')}] {m.get('name')}")
            for f in m.get("contents", []) or []:
                if f.get("type") == "file":
                    size_kb = (f.get("filesize") or 0) // 1024
                    lines.append(f"    file: {f.get('filename')} ({size_kb} KB) file_url={f.get('fileurl')}")
    return "\n".join(lines) or "Course is empty."


@tool(
    name="moodle_read_file",
    description=(
        "Download a Moodle file (from moodle_course_contents) and return its text. Supports PDF and "
        "plain-text files. For long PDFs, read a page range at a time."
    ),
    properties={
        "file_url": {"type": "string"},
        "start_page": {"type": "integer", "description": "PDF only, 1-based. Default 1."},
        "end_page": {"type": "integer", "description": "PDF only, inclusive. Default: as many as fit."},
    },
    required=["file_url"],
    enabled=_enabled,
    setup_hint=SETUP,
)
async def moodle_read_file(ctx: ToolContext, file_url: str, start_page: int = 1, end_page: int | None = None) -> str:
    # Only ever send the Moodle token to the Moodle server itself.
    if urlparse(file_url).netloc != urlparse(ctx.cfg.moodle_url).netloc:
        raise ToolError("Refusing to download: that URL isn't on the owner's Moodle site.")
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
        resp = await client.get(file_url, params={"token": ctx.cfg.moodle_token})
    if resp.is_redirect:
        raise ToolError("Moodle redirected the download (the file may need a browser login).")
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "")
    name = urlparse(file_url).path.rsplit("/", 1)[-1]

    if "pdf" in content_type or name.lower().endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(resp.content))
        total = len(reader.pages)
        start = max(1, int(start_page))
        end = min(total, int(end_page)) if end_page else total
        if start > total:
            raise ToolError(f"The PDF only has {total} pages.")
        parts, chars, last = [], 0, start - 1
        for i in range(start - 1, end):
            text = reader.pages[i].extract_text() or ""
            if chars + len(text) > MAX_FILE_CHARS and parts:
                break
            parts.append(f"--- page {i + 1} ---\n{text}")
            chars += len(text)
            last = i + 1
        more = f"\n\n[Showed pages {start}-{last} of {total}. Call again with start_page={last + 1} for more.]" if last < total else ""
        return f"File: {name} ({total} pages)\n<file_content>\n" + "\n".join(parts) + f"\n</file_content>{more}"

    if content_type.startswith("text/") or name.lower().endswith((".txt", ".md", ".csv")):
        text = resp.text
        more = ""
        if len(text) > MAX_FILE_CHARS:
            more = f"\n\n[Truncated: showing first {MAX_FILE_CHARS} of {len(text)} characters.]"
            text = text[:MAX_FILE_CHARS]
        return f"File: {name}\n<file_content>\n{text}\n</file_content>{more}"

    raise ToolError(f"Can't read '{name}' ({content_type or 'unknown type'}) yet - only PDF and text files are supported.")
