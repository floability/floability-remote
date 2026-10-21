"""Compatibility wrapper for the daemon application factory."""

from ..daemon.app import WebSettings, create_app

__all__ = ["WebSettings", "create_app"]
