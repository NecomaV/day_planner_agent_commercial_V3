from __future__ import annotations

import time
from typing import Optional

from app.services.ai_guard import breaker
from app.settings import settings


def transcribe_audio(
    file_path: str,
    api_key: str | None,
    model: str = "whisper-1",
    language: str | None = None,
) -> Optional[str]:
    if not api_key:
        return None
    if not breaker().is_open().allowed:
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None

    client = OpenAI(api_key=api_key, timeout=settings.AI_TIMEOUT_SEC)
    retries = max(0, int(settings.AI_RETRY_MAX))
    result = None
    for attempt in range(retries + 1):
        try:
            with open(file_path, "rb") as f:
                result = client.audio.transcriptions.create(
                    model=model,
                    file=f,
                    language=language,
                )
            break
        except Exception:
            breaker().record_error()
            if attempt >= retries:
                return None
            delay = settings.AI_RETRY_BACKOFF_SEC * (2 ** attempt)
            time.sleep(delay)

    text = getattr(result, "text", None)
    breaker().record_success()
    return text.strip() if text else None
