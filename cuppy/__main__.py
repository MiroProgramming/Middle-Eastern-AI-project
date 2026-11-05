"""Start Cuppy: `python -m cuppy`"""

from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn

from .brain import Brain
from .config import load_config
from .db import Database
from .notify import Notifier
from .scheduler import Scheduler
from .tools import ToolContext

log = logging.getLogger("cuppy")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = load_config()
    if not cfg.anthropic_api_key:
        sys.exit("ANTHROPIC_API_KEY is missing. Copy .env.example to .env and fill it in.")

    db = Database(cfg.db_path)
    notifier = Notifier(cfg)
    brain = Brain(cfg, db, ToolContext(cfg=cfg, db=db, notifier=notifier))

    started = []
    if cfg.telegram_enabled:
        from .channels.telegram_bot import start_telegram

        await start_telegram(cfg, brain, notifier)
        started.append("telegram")
    if cfg.discord_enabled:
        from .channels.discord_bot import start_discord

        await start_discord(cfg, brain, notifier)
        started.append("discord")

    from .channels.web import build_web_app

    server = uvicorn.Server(
        uvicorn.Config(build_web_app(cfg, brain, notifier), host="0.0.0.0", port=cfg.web_port, log_level="warning")
    )
    web_task = asyncio.create_task(server.serve())
    if cfg.whatsapp_enabled:
        started.append("whatsapp")

    if not started:
        log.warning("No chat app configured! Set TELEGRAM_BOT_TOKEN and/or DISCORD_BOT_TOKEN in .env")
    log.info(
        "Cuppy is up. Chat: %s | calls: %s | Google: %s | Moodle: %s | model: %s",
        ", ".join(started) or "none", cfg.calls_enabled, cfg.google_enabled, cfg.moodle_enabled, cfg.model,
    )

    await asyncio.gather(Scheduler(cfg, db, notifier, brain).run_forever(), web_task)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
