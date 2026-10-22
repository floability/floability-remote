"""Model Context Protocol adapter for Floability Remote's local daemon."""

__all__ = ["create_server", "run"]


def create_server(*args, **kwargs):
    """Lazily import the optional MCP SDK."""
    from .server import create_server as factory

    return factory(*args, **kwargs)


def run() -> None:
    """Lazily start the stdio MCP server."""
    from .server import run as serve

    serve()
