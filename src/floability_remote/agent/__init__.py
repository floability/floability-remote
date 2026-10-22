"""Built-in terminal agent using the same MCP server as external hosts."""

__all__ = ["run"]


def run(*args, **kwargs):
    """Lazily import optional PydanticAI dependencies."""
    from .main import run as run_agent

    return run_agent(*args, **kwargs)
