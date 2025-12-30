from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class Interval:
    start: dt.datetime
    end: dt.datetime

    def duration_minutes(self) -> int:
        return max(0, int((self.end - self.start).total_seconds() // 60))


@dataclass(frozen=True)
class Gap:
    start: dt.datetime
    end: dt.datetime

    def duration_minutes(self) -> int:
        return max(0, int((self.end - self.start).total_seconds() // 60))


def parse_hhmm(s: str) -> dt.time:
    s = s.strip()
    hh, mm = s.split(":")
    return dt.time(int(hh), int(mm))


def _ceil_to_next_minute(ts: dt.datetime) -> dt.datetime:
    if ts.second == 0 and ts.microsecond == 0:
        return ts
    return (ts + dt.timedelta(minutes=1)).replace(second=0, microsecond=0)


def normalize_date_str(s: Optional[str]) -> dt.date:
    if not s:
        raise ValueError("empty date")
    return dt.date.fromisoformat(s.strip())


def _combine(day: dt.date, t: dt.time) -> dt.datetime:
    return dt.datetime.combine(day, t).replace(second=0, microsecond=0)


def day_bounds(day: dt.date, routine, now: Optional[dt.datetime] = None) -> Tuple[dt.datetime, dt.datetime, dt.datetime, dt.datetime]:
    """Return (day_start, day_end, morning_start, morning_end).

    day_start = morning_end, but for 'today' clamps to max(morning_end, now_ceiled).
    day_end = bedtime - pre_sleep_buffer_min. (Assumes bedtime same day; if bedtime <= wake, treated as next day.)
    """
    wake = _combine(day, parse_hhmm(routine.sleep_target_wakeup))
    bed = _combine(day, parse_hhmm(routine.sleep_target_bedtime))
    if bed <= wake:
        bed = bed + dt.timedelta(days=1)

    morning_start = wake
    morning_end = wake + dt.timedelta(minutes=routine.post_wake_buffer_min)
    day_start = morning_end

    day_end = bed - dt.timedelta(minutes=routine.pre_sleep_buffer_min)

    if now is not None and now.date() == day:
        day_start = max(day_start, _ceil_to_next_minute(now))

    return day_start, day_end, morning_start, morning_end


def merge_intervals(intervals: Iterable[Interval]) -> List[Interval]:
    items = sorted([i for i in intervals if i.end > i.start], key=lambda x: x.start)
    if not items:
        return []
    merged = [items[0]]
    for cur in items[1:]:
        last = merged[-1]
        if cur.start <= last.end:
            merged[-1] = Interval(last.start, max(last.end, cur.end))
        else:
            merged.append(cur)
    return merged


def _task_to_busy_interval(task, routine) -> Optional[Interval]:
    if not task.planned_start or not task.planned_end:
        return None

    s = task.planned_start
    e = task.planned_end

    # Meals: buffer after
    if getattr(task, "kind", None) == "meal":
        e = e + dt.timedelta(minutes=routine.meal_buffer_after_min)

    # Workouts: travel before/after
    if getattr(task, "kind", None) == "workout":
        travel = dt.timedelta(minutes=routine.workout_travel_oneway_min)
        s = s - travel
        e = e + travel

    return Interval(s, e)


def build_busy_intervals(scheduled_tasks: Iterable, routine) -> List[Interval]:
    intervals: List[Interval] = []
    for t in scheduled_tasks:
        iv = _task_to_busy_interval(t, routine)
        if iv:
            intervals.append(iv)
    return merge_intervals(intervals)


def gaps_from_busy(busy: List[Interval], start: dt.datetime, end: dt.datetime) -> List[Gap]:
    if end <= start:
        return []
    if not busy:
        return [Gap(start, end)]

    busy = merge_intervals(busy)
    gaps: List[Gap] = []
    cursor = start

    for iv in busy:
        if iv.end <= start:
            continue
        if iv.start >= end:
            break
        iv_s = max(iv.start, start)
        iv_e = min(iv.end, end)
        if iv_s > cursor:
            gaps.append(Gap(cursor, iv_s))
        cursor = max(cursor, iv_e)

    if cursor < end:
        gaps.append(Gap(cursor, end))

    return gaps


def task_display_minutes(task, routine) -> int:
    """Minutes shown in the plan (without travel)."""
    est = int(getattr(task, "estimate_minutes", 0) or 0)
    if getattr(task, "kind", None) == "workout":
        return max(est, int(routine.workout_block_min))
    return est if est > 0 else 30


def format_gap_options(task, gaps: List[Gap], routine, day: dt.date) -> str:
    mins = task_display_minutes(task, routine)
    lines = []
    lines.append(f"Time slots for task (id={task.id}): {task.title} ~ {mins} min")
    lines.append(f"Date: {day.isoformat()}\n")

    if not gaps:
        lines.append("No free slots in this window.")
        return "\n".join(lines)

    lines.append("Pick a slot and optional time within it:")
    lines.append(f"/place {task.id} <slot#> [HH:MM]\n")

    for idx, g in enumerate(gaps, start=1):
        if task.kind == "workout":
            travel = dt.timedelta(minutes=routine.workout_travel_oneway_min)
            core = dt.timedelta(minutes=max(task.estimate_minutes, routine.workout_block_min))
            earliest = g.start + travel
            latest = g.end - (core + travel)
            fit = latest >= earliest
            fit_txt = "OK" if fit else "does not fit"
            lines.append(
                f"{idx}) {g.start.strftime('%H:%M')}-{g.end.strftime('%H:%M')} ({g.duration_minutes()}m) | "
                f"start range: {earliest.strftime('%H:%M')}-{latest.strftime('%H:%M')} [{fit_txt}]"
            )
        else:
            core = dt.timedelta(minutes=mins)
            latest = g.end - core
            fit = latest >= g.start
            fit_txt = "OK" if fit else "does not fit"
            lines.append(
                f"{idx}) {g.start.strftime('%H:%M')}-{g.end.strftime('%H:%M')} ({g.duration_minutes()}m) | "
                f"start range: {g.start.strftime('%H:%M')}-{latest.strftime('%H:%M')} [{fit_txt}]"
            )

    return "\n".join(lines)
