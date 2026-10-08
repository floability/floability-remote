"""Terminal presentation of workflow events."""

import sys

from .events import Event, EventKind


class CliReporter:
    """Print concise progress by default and raw remote output on request."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def emit(self, event: Event) -> None:
        kind = event.kind
        if kind == EventKind.STEP:
            print(f"[remote] [{event.step}/{event.total}] {event.message}", flush=True)
        elif kind == EventKind.DETAIL:
            self._detail(event.message)
        elif kind == EventKind.PROGRESS:
            # Verbose mode already shows the raw line that produced this event.
            if not self.verbose:
                self._detail(event.message)
        elif kind == EventKind.LOG:
            if self.verbose:
                print(event.message, end="", flush=True)
        elif kind == EventKind.READY:
            self._ready(event.data.get("url", ""))
        elif kind == EventKind.WARNING:
            print(f"[remote] WARNING: {event.message}", file=sys.stderr, flush=True)
        elif kind == EventKind.CANCELLING:
            print(f"\n[remote] {event.message}", flush=True)
        # Terminal outcome events are reported by the CLI's exit status and
        # error handling, matching the output that preceded structured events.

    @staticmethod
    def _detail(message: str) -> None:
        print(f"[remote]       {message}", flush=True)

    @staticmethod
    def _ready(url: str) -> None:
        print("\n[remote] READY — open this URL in your local browser:", flush=True)
        print(url, flush=True)
        print("\nPress Ctrl+C here when you are finished.\n", flush=True)
