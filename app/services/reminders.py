from __future__ import annotations

from app.i18n.core import t


def format_reminder_message(tasks: list, *, locale: str = "ru") -> str:
    lines = [t("reminders.header", locale=locale)]
    for task in tasks:
        when = task.planned_start or task.due_at
        when_str = when.strftime("%H:%M") if when else t("reminders.time_unknown", locale=locale)
        lines.append(
            t(
                "reminders.item",
                locale=locale,
                title=task.title,
                when=when_str,
                task_id=task.id,
            )
        )
    return "\n".join(lines)
