# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Conventions

- Reply to the user in Traditional Chinese (繁體中文) always.
- Commit messages are written in English.
- Deployment platform is Railway, project **wonderful-rejoicing**.
- Alembic migrations run only via Railway's pre-deploy command, never locally — this avoids encoding issues. Write/edit migration files locally, but do not run `alembic upgrade head` on your own machine; let the Railway deploy step apply it.

## What this is

A LINE bot for a small team to manage shared to-dos and recurring reminders. FastAPI backend, LINE Messaging API for chat commands, a LIFF (LINE Front-end Framework) web form for creating to-dos with photo attachments, and APScheduler for time-based push notifications. Deployed on Railway with Postgres.

## Commands

```bash
# install deps
pip install -r requirements.txt

# run locally (reads .env via python-dotenv)
uvicorn main:app --reload --port 8000

# create a new migration after changing database.py models
alembic revision -m "description" 
# then hand-edit the generated file in alembic/versions/ (see existing ones for style —
# migrations here are written manually, not via --autogenerate)

# do NOT run `alembic upgrade head` locally — see Conventions above.
# Migrations are applied by Railway's pre-deploy command on deploy.
```

There is no test suite, linter, or build step configured in this repo.

### Required environment variables (see `.env.example`)

- `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN` — LINE Messaging API credentials
- `LINE_USER_ID` — comma-separated list of LINE user IDs authorized to use bot commands (`AUTHORIZED_USER_IDS` in `line_handler.py`); also used as the fallback push target for the daily/recurring cron jobs in `scheduler.py`
- `DATABASE_URL` — Postgres connection string (`postgres://` is auto-rewritten to `postgresql://`); `database.py` calls `sys.exit` at import time if this is missing

## Architecture

Four modules, no package structure — everything is a top-level `.py` file imported directly.

- **`main.py`** — FastAPI app. Owns three surfaces:
  - `POST /webhook` — LINE webhook entrypoint; verifies signature, dispatches text messages to `line_handler.handle_message`
  - `GET /`, `POST /add`, `POST /delete/{id}`, `POST /recurring/*` — a server-rendered HTML admin view (`templates/index.html`) for browsing/deleting to-dos and recurring reminders directly (bypasses LINE)
  - `GET /liff` + `POST /api/todos`, `GET /api/members` — the LIFF form flow (`templates/liff.html`) and its JSON API, used when a user taps "新增" in chat
- **`line_handler.py`** — parses chat commands (all in Traditional Chinese, e.g. `清單`, `完成 3`, `定期新增 每週一 ...`) and replies via the LINE reply API. Every inbound message upserts a `Member` row (LINE user ID → display name) via `get_profile`, regardless of authorization. Only IDs in `AUTHORIZED_USER_IDS` can actually issue commands; others get a rejection reply.
- **`database.py`** — SQLAlchemy models (`Todo`, `RecurringReminder`, `Member`) and session/engine setup. `init_db()` runs `create_all` on startup (in `main.py`'s lifespan) *in addition to* Alembic migrations — schema changes still need a matching migration file for production, since `create_all` only fills in what's fully missing.
- **`scheduler.py`** — an in-process APScheduler `BackgroundScheduler` (not Celery/cron on the OS) started in `main.py`'s lifespan and shut down on app shutdown. Three jobs:
  - `check_and_notify` (daily 08:00 Asia/Taipei) — pushes to `LINE_USER_ID` when a `Todo.due_date` is exactly 30/7/3/1 days out
  - `check_recurring_reminders` (daily 08:00 Asia/Taipei) — pushes to `LINE_USER_ID` for any `RecurringReminder` matching today's weekday/day-of-month
  - `check_and_send_notifications` (every 1 minute) — the LIFF-driven per-to-do reminder path: finds `Todo` rows with `notify_enabled` and an unfired `notify_time` within a ±90s window, batches by recipient (`notify_targets` JSON list, or `"all"` meaning every `Member`), sends one combined push per user, then stamps `notified_at` so it isn't resent

### Notification paths — two independent systems

There are two distinct ways a to-do triggers a LINE push, and they don't share code:
1. **Fixed-day reminders** (`scheduler.py: check_and_notify`) — hardcoded thresholds (30/7/3/1 days before `due_date`), always pushes to `LINE_USER_ID`, applies to every to-do unconditionally.
2. **Per-to-do scheduled/immediate notifications** — set up via the LIFF form (`notify_enabled`/`notify_offset`/`notify_targets` on `Todo`). Either fired immediately at creation time (`main.py: _push_immediate_notification`, bypasses the scheduler entirely) or picked up later by the minute-interval `check_and_send_notifications` job once `notify_time` arrives.

When changing notification behavior, check both paths — a fix in one does not apply to the other.

### LIFF form and photo upload

`templates/liff.html` is a LIFF app (hardcoded `LIFF_ID` in the template) that runs inside the LINE in-app browser. It uploads photos **directly from the browser to Cloudinary** (unsigned upload preset, hardcoded cloud name/preset in the template) and only sends the resulting `photo_url` to the backend — the FastAPI server never handles image bytes. `created_by` is populated from the LIFF SDK's LINE profile, not from a login system.

### Migrations

Migrations under `alembic/versions/` are written by hand against `database.py`'s model definitions, not generated via `alembic revision --autogenerate` (there's no autogenerate config in `alembic/env.py` — `target_metadata` is set but offline/online modes both just run whatever's in the script). When adding a column, write the migration to match the new `Column(...)` in `database.py` yourself.
