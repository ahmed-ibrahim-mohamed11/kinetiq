"""Shared logging setup for ai.core and the future edge agent (Phase 5).

The research/evaluation path keeps its original print() output (part of the frozen behaviour
contract); new code (edge agent, services) should use get_logger().
"""
from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(h)
        logger.setLevel(level)
        logger.propagate = False
    return logger
