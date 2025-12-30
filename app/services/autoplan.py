from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Tuple

from app import crud
from app.services.slots import Interval, build_busy_intervals, day_bounds, gaps_from_busy


def _find_first_fit_start(
    gaps: List[Tuple[dt.datetime, dt.datetime]],
    duration: dt.timedelta,
    earliest: dt.datetime,
) -> Optional[dt.datetime]:
    """Return earliest start >= earliest that fits wholly within some gap."""
    for gs, ge in gaps:
        if ge <= earliest:
            continue
        start = max(gs, earliest)
        if start + duration <= ge:
            return start
    return None


def _gap_tuples(gaps) -> List[Tuple[dt.datetime, dt.datetime]]:
    return [(g.start, g.end) for g in gaps]


def _now_local_naive() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def _has_recent_workout(db, user_id: int, day: dt.date, rest_days: int) -> bool:
    if rest_days <= 0:
        return False
    for offset in range(1, rest_days + 1):
        prev_day = day - dt.timedelta(days=offset)
        tasks = crud.list_scheduled_for_day(db, user_id, prev_day)
        if any(t.kind == "workout" for t in tasks):
            return True
    return False


def ensure_day_anchors(db, user_id: int, day: dt.date, routine) -> None:
    """Create/update anchors for a day: morning + meals.

    Anchors are positioned using gaps within their allowed windows and reserve their buffers
    in the internal busy timeline.
    """
    now = _now_local_naive()
    day_start, _day_end, morning_start, morning_end = day_bounds(day, routine, now=now)

    # Base busy: already scheduled tasks (including existing anchors)
    scheduled = crud.list_scheduled_for_day(db, user_id, day)
    busy = build_busy_intervals(scheduled, routine)

    # Morning anchor is fixed at wake time.
    crud.upsert_anchor(
        db,
        user_id,
        anchor_key="morning_start",
        title="Morning start",
        kind="morning",
        planned_start=morning_start,
        planned_end=morning_end,
    )

    # After inserting morning, refresh busy
    scheduled = crud.list_scheduled_for_day(db, user_id, day)
    busy = build_busy_intervals(scheduled, routine)

    # Meals: find first fit within their window, reserving meal buffer after.
    meal_defs = [
        ("breakfast", "Breakfast", routine.breakfast_window_start, routine.breakfast_window_end, routine.meal_duration_min),
        ("lunch", "Lunch", routine.lunch_window_start, routine.lunch_window_end, routine.meal_duration_min),
        ("dinner", "Dinner", routine.dinner_window_start, routine.dinner_window_end, routine.meal_duration_min),
    ]

    for key, title, w_start, w_end, dur_min in meal_defs:
        ws = dt.datetime.combine(day, dt.time.fromisoformat(w_start)).replace(second=0, microsecond=0)
        we = dt.datetime.combine(day, dt.time.fromisoformat(w_end)).replace(second=0, microsecond=0)
        # For today, do not place meals in the past.
        if day == now.date():
            ws = max(ws, day_start)

        gaps = gaps_from_busy(busy, ws, we)
        slot = _find_first_fit_start(_gap_tuples(gaps), dt.timedelta(minutes=dur_min), earliest=ws)
        if not slot:
            continue

        start = slot
        end = start + dt.timedelta(minutes=dur_min)
        crud.upsert_anchor(
            db,
            user_id,
            anchor_key=key,
            title=title,
            kind="meal",
            planned_start=start,
            planned_end=end,
        )

        # Reserve busy including meal buffer
        busy.append(
            Interval(
                start=start,
                end=end + dt.timedelta(minutes=routine.meal_buffer_after_min),
            )
        )
        busy = build_busy_intervals(
            crud.list_scheduled_for_day(db, user_id, day),
            routine,
        )


def autoplan_days(
    db,
    user_id: int,
    routine,
    *,
    days: int = 1,
    start_date: Optional[dt.date] = None,
) -> List[Dict]:
    """Schedule backlog tasks into free gaps for N days."""
    if days <= 0:
        return []

    now = _now_local_naive()
    start_date = start_date or now.date()
    results: List[Dict] = []

    for offset in range(days):
        day = start_date + dt.timedelta(days=offset)

        ensure_day_anchors(db, user_id, day, routine)

        # Day bounds: awake window
        day_start, day_end, _morn_s, _morn_e = day_bounds(day, routine, now=now)

        # Build busy timeline from already scheduled tasks
        scheduled_today = crud.list_scheduled_for_day(db, user_id, day)
        busy = build_busy_intervals(scheduled_today, routine)

        # Backlog tasks
        backlog = crud.list_backlog(db, user_id)

        placed = 0
        for task in backlog:
            if task.is_done:
                continue

            is_workout = task.kind == "workout"
            if is_workout:
                if not routine.workout_enabled:
                    continue
                if routine.workout_no_sunday and day.weekday() == 6:
                    continue
                if _has_recent_workout(db, user_id, day, routine.workout_rest_days):
                    continue

            # Recompute gaps each time (simple and robust)
            gaps = gaps_from_busy(busy, day_start, day_end)
            gap_tuples = _gap_tuples(gaps)

            if is_workout:
                travel = dt.timedelta(minutes=routine.workout_travel_oneway_min)
                core = dt.timedelta(minutes=max(task.estimate_minutes, routine.workout_block_min))
                total = core + travel + travel

                # Depart time must fit within gaps
                depart = _find_first_fit_start(
                    gap_tuples,
                    duration=total,
                    earliest=day_start,
                )
                if not depart:
                    continue

                in_gym_start = depart + travel
                in_gym_end = in_gym_start + core

                crud.update_task_fields(
                    db,
                    user_id,
                    task.id,
                    planned_start=in_gym_start,
                    planned_end=in_gym_end,
                    schedule_source="autoplan",
                )

                # Reserve depart..depart+total as busy interval
                busy.append(Interval(depart, depart + total))
                busy = build_busy_intervals(crud.list_scheduled_for_day(db, user_id, day), routine)
                placed += 1
                continue

            # Regular task
            dur = dt.timedelta(minutes=int(task.estimate_minutes or 30))
            start = _find_first_fit_start(gap_tuples, duration=dur, earliest=day_start)
            if not start:
                continue

            end = start + dur
            crud.update_task_fields(
                db,
                user_id,
                task.id,
                planned_start=start,
                planned_end=end,
                schedule_source="autoplan",
            )

            busy.append(Interval(start, end))
            busy = build_busy_intervals(crud.list_scheduled_for_day(db, user_id, day), routine)
            placed += 1

        results.append(
            {
                "date": day.isoformat(),
                "anchors": len([t for t in scheduled_today if t.task_type == "anchor"]),
                "scheduled": placed,
            }
        )

    return results
