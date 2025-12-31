import re


def _normalize_task_title(title: str) -> str:
    cleaned = re.sub(r"\s+", " ", title).strip()
    cleaned = re.sub(r"^(задача|задачи)\b[:\s]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(пожалуйста|пж|плиз|пожалуй)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
    return cleaned or title


def _shorten_title(title: str, max_words: int = 6) -> str:
    words = [w for w in title.split() if w]
    if len(words) <= max_words:
        return title
    return " ".join(words[:max_words])


normalize_task_title = _normalize_task_title
shorten_title = _shorten_title
