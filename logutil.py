"""Tiny structured-logging helper: ``event=name key=value ...`` lines.

Callers must only pass identifiers/counters - never message content or file IDs.
"""
import json
import logging


def _fmt(value) -> str:
    text = str(value)
    if not text or any(c.isspace() or c == '"' for c in text):
        return json.dumps(text)
    return text


def log_event(logger: logging.Logger, level: int, event: str, **fields) -> None:
    if not logger.isEnabledFor(level):
        return
    parts = [f"event={event}"] + [f"{k}={_fmt(v)}" for k, v in fields.items()]
    logger.log(level, " ".join(parts))
