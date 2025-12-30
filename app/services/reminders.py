from __future__ import annotations


def format_reminder_message(tasks: list) -> str:
    lines = ["Upcoming tasks:"]
    for t in tasks:
        when = t.planned_start or t.due_at
        when_str = when.strftime("%H:%M") if when else "soon"
        lines.append(f"- {t.title} at {when_str} (id={t.id})")
    return "\n".join(lines)
