import datetime as dt

from app.services.slots import task_display_minutes


def _render_day_plan(tasks, backlog, day: dt.date, routine) -> str:
    lines = []
    lines.append(f"План на {day.isoformat()}:\n")

    if tasks:
        for i, t in enumerate(tasks, start=1):
            s = t.planned_start.strftime("%H:%M")
            e = t.planned_end.strftime("%H:%M")
            extra = ""
            if t.kind == "workout":
                extra = f" (дорога: {routine.workout_travel_oneway_min}м в одну сторону)"
            tag = f" [{t.kind}]" if t.kind else ""
            status = "[x]" if t.is_done else "[ ]"
            lines.append(f"{status} {i}) {s}-{e} {t.title}{tag} (id={t.id}){extra}")
    else:
        lines.append("(нет запланированных задач)")

    if backlog:
        lines.append("\nБэклог:")
        for i, t in enumerate(backlog, start=1):
            mins = task_display_minutes(t, routine)
            lines.append(f"[ ] {i}) {t.title} ~ {mins}м (id={t.id})")
        lines.append("\nПодсказка: /autoplan 1")

    return "\n".join(lines)


def _format_conflict_prompt(conflicts: list) -> str:
    lines = ["Похоже, на это время уже есть задачи:"]
    for task in conflicts[:3]:
        lines.append(f"- {task.planned_start.strftime('%H:%M')}-{task.planned_end.strftime('%H:%M')} {task.title}")
    lines.append("Что сделать? 1) заменить, 2) перенести это, 3) вставить со сдвигом")
    return "\n".join(lines)


render_day_plan = _render_day_plan
conflict_prompt = _format_conflict_prompt


def schedule_offer(day: dt.date, start: dt.datetime, end: dt.datetime) -> str:
    return (
        f"Нашел слот {day.isoformat()} {start.strftime('%H:%M')}-{end.strftime('%H:%M')}. "
        "Поставить? (да/нет)"
    )

