# Cuppy 🚗☕

Your personal AI assistant and companion. Cuppy lives on a small cloud server, talks to you on
Telegram, Discord and WhatsApp, can phone you, remembers things about you, reminds you on time,
reads your email/calendar/Moodle, and helps you study medicine and code.

Built on Claude (Anthropic API), designed to stay **cheap** while still being smart.

---

## What Cuppy can do (v1)

| Ability | How |
|---|---|
| Chat with a personality | Telegram, Discord (DMs), WhatsApp - one shared memory across all of them |
| Remember you | Saves facts about you automatically (people, exams, preferences, weak topics) |
| Reminders | "Remind me tomorrow at 9 to…", repeating (daily/weekdays/weekly/monthly) |
| Phone you | Reminders or "call me now" ring your real phone and read the message aloud (Twilio) |
| Morning briefing | Daily message with your calendar, important email, Moodle deadlines, reminders |
| Email | Search and read Gmail, create **drafts** (never sends on its own) |
| Calendar | Read and add Google Calendar events |
| Moodle | Courses, upcoming deadlines, lecture files (reads PDFs so it can quiz you on them) |
| Web search | Current information when needed |
| Study partner | Active-recall quizzing, clinical vignettes, tracks topics you struggle with |

Chat commands: `/deep <question>` (smartest brain for one hard question), `/new` (fresh
conversation), `/usage` (this month's AI cost), `/help`.

---

## What it costs

You pay for the AI per use. **Your Claude.ai or ChatGPT subscription does not cover this.**
The API is billed separately, pay-as-you-go, at console.anthropic.com.

Cuppy uses three brains so you only pay for intelligence when you need it:

| Role | Model | Price per 1M tokens (in / out) |
|---|---|---|
| Everyday chat + tools | Claude Sonnet 5.5 | $2 / $10 |
| `/deep` hard questions | Claude Opus 5.5 | $4 / $20 |
| Background summaries (and the budget fallback) | Claude Haiku 5.5 | $0.10 / $0.50 |

Built-in savings:
- **Prompt caching**: the parts that repeat on every message (personality, tools, earlier
  messages) are billed at 10% of the normal input price.
- **Short sessions**: after 40 messages, Haiku summarises the chat and a fresh session starts,
  so Cuppy isn't re-reading a huge history every time.
- **Monthly budget cap** (`MONTHLY_BUDGET_USD`): once reached, Cuppy switches to Haiku until the
  next month and tells you.
- `/usage` shows what you've spent.

**Rough estimate:** about **$10-20/month** with ~30 messages a day on Sonnet. Under **$2/month**
if you set `CUPPY_MODEL=claude-haiku-5-5`. These are estimates; check `/usage` after a week.
Other costs: a server (~$5/month), and optionally Twilio (a phone number costs ~$1-2/month, plus
per-minute call and per-message WhatsApp fees that depend on your country).

---

## Setup, step by step

### 1. Get an Anthropic API key
console.anthropic.com → API Keys → Create key. Add ~$10 credit and set a spend limit there too.

### 2. Create your Telegram bot (5 minutes)
1. In Telegram, message **@BotFather** → `/newbot` → name it "Cuppy" → copy the token.
2. You'll get your owner ID in step 4.

### 3. Get a cloud server
Any small Linux VPS works (Hetzner, DigitalOcean, etc., about $5/month, 1 GB RAM is plenty).
Install Docker and Git on it:
```bash
curl -fsSL https://get.docker.com | sh
git clone https://github.com/MiroProgramming/Middle-Eastern-AI-project.git cuppy && cd cuppy
```

### 4. Configure and start
```bash
cp .env.example .env
nano .env        # fill in ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, OWNER_NAME, TIMEZONE (e.g. Africa/Cairo)
docker compose up -d --build
```
Message your bot on Telegram. It replies with your user ID. Put it in `.env` as
`TELEGRAM_OWNER_ID=...`, then `docker compose up -d` again. **Only you can talk to Cuppy from now on.**

Logs: `docker compose logs -f`

> To try it on your own computer first: `python -m venv .venv && source .venv/bin/activate &&
> pip install -r requirements.txt && python -m cuppy`

### 5. Gmail + Google Calendar (optional)
1. console.cloud.google.com → create a project → enable **Gmail API** and **Google Calendar API**.
2. *OAuth consent screen*: External, add yourself as a test user.
3. *Credentials* → Create OAuth client ID → **Desktop app** → download `client_secret.json`.
4. On **your computer** (it opens a browser):
   `pip install -r requirements.txt && python scripts/google_auth.py client_secret.json`
5. Copy the created `data/google_token.json` to the server's `cuppy/data/` folder and restart.

### 6. Moodle (optional)
```bash
python scripts/moodle_token.py https://moodle.youruniversity.edu
```
Put the printed `MOODLE_URL` and `MOODLE_TOKEN` in `.env`. If your university uses Microsoft or
Google sign-in and this fails, look in Moodle → Preferences → **Security keys** for the "Moodle
mobile web service" key. Some universities disable this; if so, Moodle won't work.

### 7. Phone calls (optional, Twilio)
1. Sign up at twilio.com, buy a phone number with **Voice**.
2. Fill `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `OWNER_PHONE_NUMBER`
   (all numbers like `+201001234567`).
3. Try it: "call me in 2 minutes and tell me to drink water".

On a Twilio trial account you must verify your own number first, and calls start with a short
trial message.

### 8. WhatsApp (optional, Twilio)
- **Testing:** Twilio Console → Messaging → *Try WhatsApp* (sandbox). Send the join code from your
  phone. Use the sandbox number as `TWILIO_WHATSAPP_NUMBER`.
- **Incoming messages** need a public HTTPS address. Point a domain at the server and run
  [Caddy](https://caddyserver.com) in front:
  `caddy reverse-proxy --from cuppy.yourdomain.com --to localhost:8080`
  Set `PUBLIC_BASE_URL=https://cuppy.yourdomain.com` and in Twilio set the sandbox's "When a
  message comes in" URL to `https://cuppy.yourdomain.com/twilio/whatsapp`.
- **Limitation:** WhatsApp only lets businesses message you freely within 24 hours of your last
  message. Outside that window, reminders on WhatsApp may not arrive, which is why Telegram is the
  default reminder channel. A permanent WhatsApp number requires registering a WhatsApp Business
  sender with Meta through Twilio.

### 9. Discord (optional)
discord.com/developers → New Application → Bot → copy the token, enable **Message Content Intent**.
OAuth2 → URL Generator → scope `bot` → open the link to add it to a server you're in, then DM it.
It replies with your ID for `DISCORD_OWNER_ID`.

---

## Security, please read
- Cuppy has access to your email, calendar and university account. Keep `.env` and `data/`
  private, and never commit them (they're in `.gitignore`).
- It only answers **your** Telegram/Discord/WhatsApp account.
- It **cannot send email**, only draft. It can only call or message **you**.
- Emails and web pages can contain text designed to trick AI ("ignore your instructions and…").
  Cuppy is told to treat that content as data, and its tools are limited so the damage is small,
  but stay alert.
- **Old leaked key:** this repo's git history (from 2023) contains a committed `.env` file. If it
  held an OpenAI API key, revoke that key at platform.openai.com.

---

## How it's built
```
cuppy/
  __main__.py        starts everything
  brain.py           talks to Claude, runs tools, caching, model tiers, cost tracking
  personality.py     who Cuppy is - edit to change the personality
  scheduler.py       delivers reminders + morning briefing
  notify.py          sends messages/calls to you, with fallback between channels
  db.py              SQLite: memories, reminders, conversation, usage
  tools/             abilities (memory, reminders, contact, Google, Moodle)
  channels/          Telegram, Discord, WhatsApp webhook
scripts/             one-time Google + Moodle setup
tests/               offline tests (no API key needed): pytest
```
To give Cuppy a new ability, add a function with `@tool(...)` in `cuppy/tools/` (see
`memory_tools.py` for the pattern).

## Roadmap
- **Phase 2:** voice notes (send Cuppy audio, get audio back), a custom voice (e.g. ElevenLabs),
  two-way phone conversations instead of read-only calls.
- **Phase 3:** Anki flashcards generated from lecture PDFs, spaced-repetition quiz schedule.
- **Phase 4:** the car 🚗, if it has a connected-car API (many brands do), plus smart-home
  control through tools like Home Assistant or n8n.

## Development
```bash
pip install -r requirements.txt pytest
pytest
```
