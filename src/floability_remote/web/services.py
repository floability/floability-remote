"""Compatibility wrapper for the application runtime."""

from ..daemon.dependencies import Services, get_runtime, get_services

__all__ = ["Services", "get_runtime", "get_services"]
