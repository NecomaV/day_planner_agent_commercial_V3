# Day Planner Agent (commercial-ready foundation)

This repo is a **commercial-ready baseline** (clean architecture, migrations, multi-user by Telegram, idempotency).

## Requirements
- Windows / macOS / Linux
- **Python 3.12 or 3.13** (recommended).  
  Do **NOT** use Python 3.14 yet: some deps (e.g., `pydantic-core`) may not have prebuilt wheels and will require Rust/Cargo.

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

We do **not** ship a real `.env` file in a commercial repo (tokens must never be committed).  
Copy the example and fill it:

```bash
copy .env.example .env   # Windows PowerShell: Copy-Item .env.example .env
# or:
cp .env.example .env
```

## 3) Create / migrate DB (Alembic)

```bash
python -m scripts.init_db
```

This runs: `alembic upgrade head`.

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
- `/start` — register (multi-user)
- `/todo <minutes> <text>` — create a backlog task
- `/plan` — show plan (scheduled + backlog)
- `/autoplan <days>` — schedule backlog tasks + ensure anchors (idempotent)
- `/done <id>` — mark done
- `/delete <id>` — delete task
- `/routine show` — show routine config

## Key concepts implemented

### Task types (normal types)
- `task_type`:
  - `user` — created by user (/todo)
  - `anchor` — daily fixed items (morning start + meals)
  - `system` — reserved for future system-generated items
- `kind`:
  - `workout`, `meal`, `morning`, `work`, `other`

### Migrations (Alembic)
All schema changes go through migrations:
- `alembic/versions/0001_initial.py`

### Multi-user
- Every Telegram chat = separate user (stored in `users.telegram_chat_id`).
- All queries are scoped by `user_id` (no cross-user data leaks).

### Idempotency
- Anchors are **UPSERTed** via `anchor_key` (`user_id + anchor_key` unique).
- User-created tasks support optional `idempotency_key` (used in Telegram via message_id).
- `/autoplan` is **non-destructive**:
  - it **does not delete** previously scheduled tasks
  - it only schedules tasks that are still unscheduled

### Workout travel buffer (fix)
Workout scheduling reserves travel time using:
- `routine_configs.workout_travel_oneway_min` (default 15)

So a 60-minute workout becomes:
- min(gym block) = 120 minutes
- plus travel reserved before/after
- autoplan ensures start is not placed “immediately after breakfast”.

## Commercial hardening roadmap (next)
- Auth for API (JWT / API keys)
- Billing (Stripe)
- Tenant isolation at API level (not only Telegram)
- Observability (structured logs, Sentry)
- Background jobs (Celery/RQ) + reminders
