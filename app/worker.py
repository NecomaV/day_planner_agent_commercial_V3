from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging

from app.bot.telegram import get_bot
from app.crud import list_due_reminders, mark_reminder_sent, record_reminder_failure
from app.db import SessionLocal

POLL_INTERVAL_SEC = 5

logger = logging.getLogger("day_planner_worker")


async def _send_reminder(bot, reminder) -> None:
    if reminder.channel != "telegram":
        raise ValueError(f"Unsupported channel: {reminder.channel}")
    payload = json.loads(reminder.payload_json or "{}")
    chat_id = payload.get("chat_id")
    text = payload.get("text")
    if not chat_id or not text:
        raise ValueError("Reminder payload must include chat_id and text.")
    await bot.send_message(chat_id=chat_id, text=text)


async def _run_once() -> int:
    now = dt.datetime.utcnow()
    bot = get_bot()
    processed = 0
    with SessionLocal() as db:
        reminders = list_due_reminders(db, now)
        for reminder in reminders:
            try:
                await _send_reminder(bot, reminder)
            except Exception as exc:  # noqa: BLE001
                record_reminder_failure(db, reminder, str(exc))
                continue
            mark_reminder_sent(db, reminder, now)
            processed += 1
        if reminders:
            db.commit()
    return processed


async def run_loop() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Reminder worker started")
    tick = 0
    while True:
        try:
            processed = await _run_once()
            tick += 1
            if tick % 12 == 0:
                logger.info("Reminder worker heartbeat (processed=%s)", processed)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Reminder worker error: %s", exc)
        await asyncio.sleep(POLL_INTERVAL_SEC)


def main() -> None:
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
