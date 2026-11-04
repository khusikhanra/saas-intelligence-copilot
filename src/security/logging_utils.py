"""Privacy-conscious logging: record that something happened and how long it took, never the question text,
SQL results, or credentials."""
from __future__ import annotations

import hashlib
import logging
import re

logger = logging.getLogger("saas_copilot")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)

_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


def redact(text: str) -> str:
    """Remove anything that looks like an API key from text destined for logs or error messages."""
    return _KEY_PATTERN.sub("sk-***REDACTED***", str(text))


def log_event(event: str, **fields) -> None:
    safe = {k: (redact(v) if isinstance(v, str) else v) for k, v in fields.items() if k not in {"question", "answer", "sql", "api_key"}}
    logger.info("%s %s", event, " ".join(f"{k}={v}" for k, v in safe.items()))


def question_fingerprint(question: str) -> str:
    """Short non-reversible fingerprint (lets you correlate repeats without storing the text)."""
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:8]
