"""Logging to `logs/brain.log` (size-rotated) and stderr."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .paths import Workspace

_CONFIGURED = set()


def get_logger(ws: Workspace, name: str = "brain") -> logging.Logger:
    logger = logging.getLogger(name)
    key = (name, str(ws.root))
    if key in _CONFIGURED:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    try:
        ws.logs.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(str(ws.logs / "brain.log"), maxBytes=1_000_000,
                                           backupCount=3, encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError:
        pass  # logging must never stop a run
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    stream.setLevel(logging.WARNING)
    logger.addHandler(stream)
    _CONFIGURED.add(key)
    return logger
