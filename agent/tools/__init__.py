"""Tool registry. Importing this package registers every tool exactly once."""

from __future__ import annotations

from . import devops, health, inventory, recall  # noqa: F401,E402  (import for side effects)
from .base import REGISTRY, Tool, dispatch, specs  # noqa: F401

__all__ = ["REGISTRY", "Tool", "dispatch", "specs", "devops", "health", "inventory", "recall"]
