from __future__ import annotations

from typing import Optional


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

    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        content = resp.choices[0].message.content or ""
        content = content.strip()
        return content or None
    except Exception:
        return None
