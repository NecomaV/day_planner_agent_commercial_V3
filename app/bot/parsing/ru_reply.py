from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2] / "resources"


@dataclass(frozen=True)
class ReplyFlags:
    is_yes: bool
    is_no: bool
    is_cancel: bool
    is_help: bool
    normalized: str


@lru_cache(maxsize=8)
def _load_list(name: str) -> set[str]:
    path = BASE_DIR / name
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        return set()
    return {str(item).strip().lower() for item in data if str(item).strip()}


def _normalize(text: str) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", text.strip().lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def parse_reply(text: str) -> ReplyFlags:
    normalized = _normalize(text)
    tokens = normalized.split() if normalized else []
    token_set = set(tokens)

    yes = _load_list("ru_affirmations.json")
    no = _load_list("ru_negations.json")
    cancel = _load_list("ru_cancel.json")
    help_words = _load_list("ru_help.json")

    yes_tokens = {item for item in yes if " " not in item}
    no_tokens = {item for item in no if " " not in item}
    cancel_tokens = {item for item in cancel if " " not in item}
    help_tokens = {item for item in help_words if " " not in item}

    yes_phrases = [item for item in yes if " " in item]
    no_phrases = [item for item in no if " " in item]
    cancel_phrases = [item for item in cancel if " " in item]
    help_phrases = [item for item in help_words if " " in item]

    is_yes = bool(token_set & yes_tokens) or any(p in normalized for p in yes_phrases)
    is_no = bool(token_set & no_tokens) or any(p in normalized for p in no_phrases)
    is_cancel = bool(token_set & cancel_tokens) or any(p in normalized for p in cancel_phrases)
    is_help = bool(token_set & help_tokens) or any(p in normalized for p in help_phrases)

    return ReplyFlags(
        is_yes=is_yes,
        is_no=is_no,
        is_cancel=is_cancel,
        is_help=is_help,
        normalized=normalized,
    )
