"""CLI entry point."""

from typing import Optional, Sequence

from .commands import dispatch
from .parser import build_parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    return dispatch(build_parser().parse_args(argv))
