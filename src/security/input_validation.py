"""Validation of user text input."""
from __future__ import annotations

import re

from src import config

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class InputError(ValueError):
    """User input rejected; message is safe to display."""


def clean_question(text: object, max_chars: int | None = None) -> str:
    max_chars = max_chars or config.MAX_QUESTION_CHARS
    if not isinstance(text, str):
        raise InputError("Please type a question.")
    text = _CONTROL.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise InputError("Please type a question.")
    if len(text) > max_chars:
        raise InputError(f"Questions are limited to {max_chars} characters (yours has {len(text)}).")
    return text
