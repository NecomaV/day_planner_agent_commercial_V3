import datetime as dt

from app.i18n.core import t
from app.services.slots import task_display_minutes


def _render_day_plan(tasks, backlog, day: dt.date, routine, locale: str = "ru") -> str:
    lines = [t("plan.header", locale=locale, date=day.isoformat())]

    if tasks:
        for i, item in enumerate(tasks, start=1):
            start = item.planned_start.strftime("%H:%M")
            end = item.planned_end.strftime("%H:%M")
            extra = ""
            if item.kind == "workout":
                extra = t(
                    "plan.workout_travel",
                    locale=locale,
                    travel_min=routine.workout_travel_oneway_min,
                )
            tag = f" [{item.kind}]" if item.kind else ""
            status = "[x]" if item.is_done else "[ ]"
            lines.append(
                t(
                    "plan.task_line",
                    locale=locale,
                    status=status,
                    index=i,
                    start=start,
                    end=end,
                    title=item.title,
                    tag=tag,
                    task_id=item.id,
                    extra=extra,
                )
            )
    else:
        lines.append(t("plan.empty", locale=locale))

    if backlog:
        lines.append("")
        lines.append(t("plan.backlog_header", locale=locale))
        for i, item in enumerate(backlog, start=1):
            mins = task_display_minutes(item, routine)
            lines.append(
                t(
                    "plan.backlog_line",
                    locale=locale,
                    index=i,
                    title=item.title,
                    minutes=mins,
                    task_id=item.id,
                )
            )
        lines.append("")
        lines.append(t("plan.autoplan_hint", locale=locale))

    return "\n".join(lines)


def _format_conflict_prompt(conflicts: list, locale: str = "ru") -> str:
    lines = [t("plan.conflict_header", locale=locale)]
    for task in conflicts[:3]:
        lines.append(
            t(
                "plan.conflict_item",
                locale=locale,
                start=task.planned_start.strftime("%H:%M"),
                end=task.planned_end.strftime("%H:%M"),
                title=task.title,
            )
        )
    lines.append(t("plan.conflict_options", locale=locale))
    return "\n".join(lines)


render_day_plan = _render_day_plan
conflict_prompt = _format_conflict_prompt


def schedule_offer(day: dt.date, start: dt.datetime, end: dt.datetime, locale: str = "ru") -> str:
    return t(
        "plan.schedule_offer",
        locale=locale,
        date=day.isoformat(),
        start=start.strftime("%H:%M"),
        end=end.strftime("%H:%M"),
    )
