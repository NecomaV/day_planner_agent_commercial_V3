from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


@lru_cache(maxsize=4)
def _load_catalog(locale: str) -> dict[str, Any]:
    path = BASE_DIR / f"{locale}.json"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


def normalize_locale(value: str | None, default: str = "ru") -> str:
    if not value:
        return default
    token = value.strip().lower()
    if "-" in token:
        token = token.split("-", 1)[0]
    if "_" in token:
        token = token.split("_", 1)[0]
    return token or default


def locale_for_user(user, default: str = "ru") -> str:
    return normalize_locale(getattr(user, "preferred_language", None), default=default)


def t(key: str, locale: str = "ru", **vars: Any) -> str:
    locale = normalize_locale(locale)
    data = _load_catalog(locale)
    template = data.get(key)
    if template is None and locale != "en":
        template = _load_catalog("en").get(key)
    if template is None:
        template = key
    if not isinstance(template, str):
        return str(template)
    return template.format_map(_SafeDict(**vars))


def t_list(key: str, locale: str = "ru") -> list[str]:
    locale = normalize_locale(locale)
    data = _load_catalog(locale)
    value = data.get(key)
    if value is None and locale != "en":
        value = _load_catalog("en").get(key)
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []
