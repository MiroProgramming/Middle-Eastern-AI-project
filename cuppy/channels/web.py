"""Small web server: a health check and the incoming-WhatsApp webhook for Twilio."""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, Request, Response

from ..brain import Brain
from ..config import Config
from ..notify import Notifier
from . import handle_text

log = logging.getLogger(__name__)
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def build_web_app(cfg: Config, brain: Brain, notifier: Notifier) -> FastAPI:
    app = FastAPI(title="Cuppy", docs_url=None, redoc_url=None, openapi_url=None)
    background: set[asyncio.Task] = set()

    @app.get("/health")
    async def health():
        return {"ok": True, "channels": notifier.available()}

    @app.post("/twilio/whatsapp")
    async def whatsapp_in(request: Request):
        if not cfg.whatsapp_enabled:
            return Response(status_code=404)
        form = dict(await request.form())
        # Make sure the request really comes from Twilio.
        from twilio.request_validator import RequestValidator

        url = f"{cfg.public_base_url}/twilio/whatsapp" if cfg.public_base_url else str(request.url)
        signature = request.headers.get("X-Twilio-Signature", "")
        if not RequestValidator(cfg.twilio_auth_token).validate(url, form, signature):
            log.warning("Rejected WhatsApp webhook with a bad signature")
            return Response(status_code=403)
        if form.get("From") != f"whatsapp:{cfg.owner_whatsapp_number}":
            log.warning("Ignored WhatsApp message from stranger %s", form.get("From"))
            return Response(content=EMPTY_TWIML, media_type="application/xml")

        async def answer(text: str):
            reply = await handle_text(brain, text, "whatsapp")
            try:
                await notifier.send_whatsapp(reply)
            except Exception:
                log.exception("Couldn't send WhatsApp reply")

        # Twilio only waits 15 seconds, so reply later through the API instead of inline.
        task = asyncio.create_task(answer(str(form.get("Body", ""))))
        background.add(task)
        task.add_done_callback(background.discard)
        return Response(content=EMPTY_TWIML, media_type="application/xml")

    return app
