"""All of Cuppy's settings, read from environment variables (or a .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _get_int(name: str) -> int | None:
    value = _get(name)
    return int(value) if value else None


@dataclass
class Config:
    # --- Brain -----------------------------------------------------------
    anthropic_api_key: str = ""
    # Everyday brain: good quality at a low price.
    model: str = "claude-sonnet-5-5"
    effort: str = "medium"  # low | medium | high | xhigh | max
    # "Deep mode" brain, used only when you ask for it (/deep): smartest, pricier.
    deep_model: str = "claude-opus-5-5"
    deep_effort: str = "high"
    # Background chores (summarising old conversations): cheapest.
    cheap_model: str = "claude-haiku-5-5"
    # Start a fresh conversation (keeping a summary) once history gets this long.
    # Shorter = cheaper, because the whole conversation is re-read on every message.
    max_history_messages: int = 40
    # Monthly spending cap in USD. Above it Cuppy switches to cheap_model. 0 = no cap.
    monthly_budget_usd: float = 0.0

    # --- About you -------------------------------------------------------
    owner_name: str = "boss"
    timezone: str = "UTC"
    owner_about: str = ""  # free text: who you are, what you study, etc.

    # --- Chat apps -------------------------------------------------------
    telegram_bot_token: str = ""
    telegram_owner_id: int | None = None
    discord_bot_token: str = ""
    discord_owner_id: int | None = None

    # --- Phone + WhatsApp (Twilio) ----------------------------------------
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""  # the number Cuppy calls from, e.g. +15551234567
    twilio_whatsapp_number: str = ""  # Cuppy's WhatsApp sender, e.g. +14155238886
    owner_phone_number: str = ""  # YOUR phone number, e.g. +201001234567
    owner_whatsapp_number: str = ""  # defaults to owner_phone_number
    twilio_voice: str = "Polly.Matthew"
    public_base_url: str = ""  # e.g. https://cuppy.example.com (needed for incoming WhatsApp)

    # --- Your accounts ---------------------------------------------------
    google_token_file: Path = field(default_factory=lambda: Path("data/google_token.json"))
    moodle_url: str = ""  # e.g. https://moodle.youruniversity.edu
    moodle_token: str = ""

    # --- Behaviour -------------------------------------------------------
    default_notify_channel: str = "telegram"  # telegram | discord | whatsapp
    briefing_time: str = ""  # "07:30" -> daily morning briefing; empty = off
    data_dir: Path = field(default_factory=lambda: Path("data"))
    web_port: int = 8080

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cuppy.db"

    # Which integrations are switched on (all are optional except the brain).
    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def discord_enabled(self) -> bool:
        return bool(self.discord_bot_token)

    @property
    def twilio_enabled(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token)

    @property
    def calls_enabled(self) -> bool:
        return self.twilio_enabled and bool(self.twilio_phone_number and self.owner_phone_number)

    @property
    def whatsapp_enabled(self) -> bool:
        return self.twilio_enabled and bool(self.twilio_whatsapp_number and self.owner_whatsapp_number)

    @property
    def google_enabled(self) -> bool:
        return self.google_token_file.exists()

    @property
    def moodle_enabled(self) -> bool:
        return bool(self.moodle_url and self.moodle_token)


def load_config() -> Config:
    load_dotenv()
    data_dir = Path(_get("DATA_DIR", "data"))
    cfg = Config(
        anthropic_api_key=_get("ANTHROPIC_API_KEY"),
        model=_get("CUPPY_MODEL", "claude-sonnet-5-5"),
        effort=_get("CUPPY_EFFORT", "medium"),
        deep_model=_get("CUPPY_DEEP_MODEL", "claude-opus-5-5"),
        deep_effort=_get("CUPPY_DEEP_EFFORT", "high"),
        cheap_model=_get("CUPPY_CHEAP_MODEL", "claude-haiku-5-5"),
        max_history_messages=int(_get("CUPPY_MAX_HISTORY", "40")),
        monthly_budget_usd=float(_get("MONTHLY_BUDGET_USD", "0") or 0),
        owner_name=_get("OWNER_NAME", "boss"),
        timezone=_get("TIMEZONE", "UTC"),
        owner_about=_get("OWNER_ABOUT"),
        telegram_bot_token=_get("TELEGRAM_BOT_TOKEN"),
        telegram_owner_id=_get_int("TELEGRAM_OWNER_ID"),
        discord_bot_token=_get("DISCORD_BOT_TOKEN"),
        discord_owner_id=_get_int("DISCORD_OWNER_ID"),
        twilio_account_sid=_get("TWILIO_ACCOUNT_SID"),
        twilio_auth_token=_get("TWILIO_AUTH_TOKEN"),
        twilio_phone_number=_get("TWILIO_PHONE_NUMBER"),
        twilio_whatsapp_number=_get("TWILIO_WHATSAPP_NUMBER"),
        owner_phone_number=_get("OWNER_PHONE_NUMBER"),
        owner_whatsapp_number=_get("OWNER_WHATSAPP_NUMBER") or _get("OWNER_PHONE_NUMBER"),
        twilio_voice=_get("TWILIO_VOICE", "Polly.Matthew"),
        public_base_url=_get("PUBLIC_BASE_URL").rstrip("/"),
        google_token_file=Path(_get("GOOGLE_TOKEN_FILE", str(data_dir / "google_token.json"))),
        moodle_url=_get("MOODLE_URL").rstrip("/"),
        moodle_token=_get("MOODLE_TOKEN"),
        default_notify_channel=_get("DEFAULT_NOTIFY_CHANNEL", "telegram"),
        briefing_time=_get("BRIEFING_TIME"),
        data_dir=data_dir,
        web_port=int(_get("WEB_PORT", "8080")),
    )
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg
