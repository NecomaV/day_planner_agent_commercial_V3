from __future__ import annotations

import time
from typing import Optional

from app.services.ai_guard import breaker
from app.settings import settings


def chat_reply(
    message: str,
    api_key: str | None,
    model: str,
    system_prompt: str,
    context_prompt: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> Optional[str]:
    if not api_key:
        return None
    if not breaker().is_open().allowed:
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if context_prompt:
        messages.append({"role": "system", "content": context_prompt})
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": message})

    client = OpenAI(api_key=api_key, timeout=settings.AI_TIMEOUT_SEC)
    retries = max(0, int(settings.AI_RETRY_MAX))
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
            )
            content = resp.choices[0].message.content or ""
            content = content.strip()
            breaker().record_success()
            return content or None
        except Exception:
            breaker().record_error()
            if attempt >= retries:
                return None
            delay = settings.AI_RETRY_BACKOFF_SEC * (2 ** attempt)
            time.sleep(delay)
