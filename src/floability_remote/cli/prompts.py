"""Terminal-only confirmation handling."""

import sys

from ..domain.errors import RemoteRunError
from ..domain.interaction import INSTALL_MINIFORGE, Confirm, ConfirmationRequest


NONINTERACTIVE_HINTS = {
    INSTALL_MINIFORGE: "Conda was not found. Re-run with --yes to install user-scoped Miniforge."
}


def terminal_confirmation(assume_yes: bool) -> Confirm:
    def confirm(request: ConfirmationRequest) -> bool:
        if assume_yes:
            return True
        if not sys.stdin.isatty():
            raise RemoteRunError(
                NONINTERACTIVE_HINTS.get(
                    request.key, f"{request.message} Re-run with --yes to approve."
                )
            )
        answer = input(f"{request.message} [y/N] ")
        return answer.strip().lower() in {"y", "yes"}

    return confirm
