# Day Planner Agent (commercial-ready foundation)

This repo is a commercial-ready baseline (clean architecture, migrations, multi-user by Telegram, idempotency).

## Requirements
- Windows / macOS / Linux
- Python 3.12 or 3.13 (recommended)
  - Do not use Python 3.14 yet: some deps may not have prebuilt wheels.

## 1) Setup

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

## 2) Configure environment

We do not ship a real `.env` file in a commercial repo (tokens must never be committed).
Copy the example and fill it:

```bash
copy .env.example .env   # Windows PowerShell: Copy-Item .env.example .env
# or:
cp .env.example .env
```

- `TELEGRAM_BOT_TOKEN` is required for the bot.
- `API_KEY` is optional. If set, the REST API requires `X-API-Key`.
- `REMINDER_LEAD_MIN` controls how many minutes before a task starts a reminder is sent.
- `CALL_FOLLOWUP_DAYS` controls default follow-up delay for `/call`.

## 3) Create / migrate DB (Alembic)

```bash
python -m scripts.init_db
```

## 4) Run API (optional)

```bash
python run_local.py
```

Open:
- `http://127.0.0.1:8000/docs`

## 5) Run Telegram bot

```bash
python run_telegram_bot.py
```

### Bot commands
- `/start` - help
- `/me` - show user id
- `/todo <minutes> <text>` - create a backlog task
- `/capture <text>` - quick task capture with date/time parsing
- `/call <name> [notes]` - log a call and create follow-up
- `/plan [YYYY-MM-DD]` - show plan (scheduled + backlog)
- `/autoplan <days> [YYYY-MM-DD]` - schedule backlog tasks + ensure anchors
- `/morning` - show today's morning routine
- `/routine_add <offset> <duration> <title> [| kind]` - add routine step
- `/routine_list` - list routine steps
- `/routine_del <step_id>` - delete routine step
- `/pantry add|remove|list <item>` - manage pantry
- `/breakfast` - suggest breakfast from pantry
- `/workout today|show|set|clear|list ...` - workout plan commands
- `/slots <id> [YYYY-MM-DD]` - show slots for a task
- `/place <id> <slot#> [HH:MM]` - place task into a slot
- `/schedule <id> <HH:MM> [YYYY-MM-DD]` - schedule by time
- `/unschedule <id>` - move to backlog
- `/done <id>` - mark done
- `/delete <id>` - delete task

## Key concepts implemented

### Task types
- `task_type`:
  - `user` - created by user (/todo)
  - `anchor` - daily fixed items (morning + meals)
  - `system` - reserved for future system-generated items
- `kind`:
  - `workout`, `meal`, `morning`, `work`, `other`

### Migrations (Alembic)
All schema changes go through migrations:
- `alembic/versions/0001_initial.py`
- `alembic/versions/0002_assistant_features.py`

### Multi-user
- Every Telegram chat = separate user (stored in `users.telegram_chat_id`).
- All queries are scoped by `user_id` (no cross-user data leaks).

### Idempotency
- Anchors are UPSERTed via `anchor_key` (`user_id + anchor_key` unique).
- User-created tasks support optional `idempotency_key` (used in Telegram via message_id).
- Routine steps use daily idempotency keys like `routine:<step_id>:YYYY-MM-DD`.
- `/autoplan` is non-destructive:
  - it does not delete previously scheduled tasks
  - it only schedules tasks that are still unscheduled

### Workout travel buffer
Workout scheduling reserves travel time using:
- `routine_configs.workout_travel_oneway_min` (default 15)

So a 60-minute workout becomes:
- min(gym block) = 120 minutes
- plus travel reserved before/after
- autoplan ensures start is not placed immediately after breakfast

## Commercial hardening roadmap (next)
- Auth for API (JWT or per-user API keys)
- Billing (Stripe)
- Tenant isolation at API level (not only Telegram)
- Observability (structured logs, Sentry)
- Background jobs (Celery/RQ) + reminders
- Rate limiting and abuse prevention
