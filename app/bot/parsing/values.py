import re


def parse_int_value(text: str) -> int | None:
    m = re.search(r"\b(\d{1,4})\b", text)
    if not m:
        return None
    return int(m.group(1))


def parse_float_value(text: str) -> float | None:
    m = re.search(r"\b(\d{1,2}(?:[.,]\d{1,2})?)\b", text)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))
