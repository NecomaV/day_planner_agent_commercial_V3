from __future__ import annotations

import logging
import re


_TOKEN_PATTERNS = [
    re.compile(r"(Authorization:\s*Bearer\s+)(\S+)", re.IGNORECASE),
    re.compile(r"(X-User-Key:\s*)(\S+)", re.IGNORECASE),
    re.compile(r"(api_token[:=]\s*)(\S+)", re.IGNORECASE),
]


def redact_text(text: str) -> str:
    redacted = text
    for pattern in _TOKEN_PATTERNS:
        redacted = pattern.sub(r"\1<redacted>", redacted)
    return redacted


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)

        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(
                redact_text(arg) if isinstance(arg, str) else arg for arg in args
            )
        elif isinstance(args, dict):
            record.args = {
                key: redact_text(value) if isinstance(value, str) else value
                for key, value in args.items()
            }
        return True
