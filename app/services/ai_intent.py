from __future__ import annotations

import json
from typing import Any, Optional


def parse_intent(text: str, api_key: str | None, model: str) -> Optional[dict[str, Any]]:
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None


def suggest_routine_steps(goal: str, api_key: str | None, model: str) -> Optional[list[str]]:
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None

    system = (
        "You generate a concise morning routine for the user's goal. "
        "Return JSON: {\"items\": [\"step1\", \"step2\", ...]} with 4-8 short steps."
    )
    try:
        client = OpenAI(api_key=api_key)
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
            return [str(i).strip() for i in items if str(i).strip()]
        return None
    except Exception:
        return None

    system = (
        "Return a JSON object that classifies the user's message. "
        "Allowed intents: task, routine, pantry_add, pantry_remove, workout_set, breakfast, plan, unknown. "
        "If intent=task, return {intent, text}. "
        "If intent=routine, return {intent, items:[...]} with routine step titles. "
        "If intent=pantry_add or pantry_remove, return {intent, items:[{name, quantity}]}. "
        "If intent=workout_set, return {intent, weekday, title, details}. "
        "If intent=breakfast or plan, return {intent}. "
        "Always return valid JSON. No extra keys."
    )

    try:
        client = OpenAI(api_key=api_key)
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
        if data.get("intent") == "unknown":
            return None
        return data
    except Exception:
        return None
