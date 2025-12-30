from __future__ import annotations

from typing import Optional


def transcribe_audio(file_path: str, api_key: str | None, model: str = "whisper-1") -> Optional[str]:
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None

    try:
        client = OpenAI(api_key=api_key)
        with open(file_path, "rb") as f:
            result = client.audio.transcriptions.create(model=model, file=f)
    except Exception:
        return None

    text = getattr(result, "text", None)
    return text.strip() if text else None
