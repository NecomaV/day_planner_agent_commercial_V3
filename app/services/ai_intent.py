from __future__ import annotations

import json
import time
from typing import Any, Optional

from app.services.ai_guard import breaker
from app.settings import settings

ALLOWED_INTENTS = {
    "task",
    "routine",
    "pantry_add",
    "pantry_remove",
    "workout_set",
    "breakfast",
    "plan",
    "clear_all",
    "command",
    "unknown",
}


def _get_client(api_key: str | None):
    if not api_key:
        return None
    if not breaker().is_open().allowed:
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None
    return OpenAI(api_key=api_key, timeout=settings.AI_TIMEOUT_SEC)


def parse_intent(text: str, api_key: str | None, model: str, locale: str = "ru") -> Optional[dict[str, Any]]:
    client = _get_client(api_key)
    if not client:
        return None

    user_language = "Russian" if locale.lower().startswith("ru") else "English"
    system = (
        "Return a JSON object that classifies the user's message. "
        "Supported languages: Russian, Ukrainian, English. "
        "Allowed intents: task, routine, pantry_add, pantry_remove, workout_set, breakfast, plan, clear_all, command, unknown. "
        "If intent=task, return {intent, text}. "
        "If intent=routine, return {intent, items:[...]} with routine step titles. "
        "If intent=pantry_add or pantry_remove, return {intent, items:[{name, quantity}]}. "
        "If intent=workout_set, return {intent, weekday, title, details}. "
        "If intent=breakfast or plan, return {intent}. "
        "If intent=clear_all, return {intent, targets:[tasks, routine]}. "
        "If intent=command, return {intent, name, args} where name is one of: "
        "plan, autoplan, morning, routine_add, routine_list, routine_del, pantry, breakfast, workout, "
        "cabinet, login, logout, done, delete, unschedule, slots, place, schedule, todo, capture, call, "
        "health, habit, task_location, delay. "
        "args must be a JSON array of strings. "
        "If the user is chatting, greeting, or asking a general question, return {intent: unknown}. "
        f"Prefer {user_language} for all text values. "
        "Always return valid JSON. No extra keys."
    )

    retries = max(0, int(settings.AI_RETRY_MAX))
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or ""
            data = json.loads(content)
            if not isinstance(data, dict):
                return None
            intent = str(data.get("intent", "")).lower()
            if intent not in ALLOWED_INTENTS or intent == "unknown":
                return None
            data["intent"] = intent
            breaker().record_success()
            return data
        except Exception:
            breaker().record_error()
            if attempt >= retries:
                return None
            delay = settings.AI_RETRY_BACKOFF_SEC * (2 ** attempt)
            time.sleep(delay)


def suggest_routine_steps(goal: str, api_key: str | None, model: str) -> Optional[list[str]]:
    client = _get_client(api_key)
    if not client:
        return None

    system = (
        "You generate a concise morning routine for the user's goal. "
        "Respond in Russian. "
        "Return JSON: {\"items\": [\"step1\", \"step2\", ...]} with 4-8 short steps."
    )
    retries = max(0, int(settings.AI_RETRY_MAX))
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": goal},
                ],
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or ""
            data = json.loads(content)
            items = data.get("items")
            if isinstance(items, list):
                breaker().record_success()
                return [str(i).strip() for i in items if str(i).strip()]
            return None
        except Exception:
            breaker().record_error()
            if attempt >= retries:
                return None
            delay = settings.AI_RETRY_BACKOFF_SEC * (2 ** attempt)
            time.sleep(delay)
