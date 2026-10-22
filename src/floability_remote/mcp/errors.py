"""Translate expected application failures into model-visible MCP tool errors."""

import functools
from typing import Callable, TypeVar, cast

from mcp.server.mcpserver.exceptions import ToolError

from ..application.errors import OperationError


FunctionT = TypeVar("FunctionT", bound=Callable)


def tool_errors(function: FunctionT) -> FunctionT:
    """Expose stable operation errors while keeping unexpected exceptions private."""

    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except OperationError as error:
            message = f"{error.code}: {error}"
            if error.code == "daemon_not_running":
                message += (
                    " Start it with `floability-remote daemon start --no-browser`."
                )
            if error.issues:
                details = "; ".join(
                    f"{item.get('field', 'input')}: {item.get('message', 'invalid')}"
                    for item in error.issues
                )
                message += f" ({details})"
            raise ToolError(message) from error

    return cast(FunctionT, wrapped)
