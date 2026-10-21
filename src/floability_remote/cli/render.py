"""Human-readable terminal rendering."""

import time
from typing import List, Optional

from ..domain.errors import RemoteRunError
from ..services.files import GROUP_LABELS, GROUP_ORDER, FileInventory, format_size
from ..services.taskvine import TaskVineStatus


def print_inventory(inventory: FileInventory) -> List:
    selectable = []
    number = 1
    if not inventory.files:
        print("\nNo downloadable files were found.")
    for group in GROUP_ORDER:
        items = [item for item in inventory.files if item.group == group]
        if not items:
            continue
        print(f"\n{GROUP_LABELS[group]}")
        for item in items:
            if item.downloadable:
                print(f"  {number:>3}. {item.path}  ({format_size(item.size)})")
                selectable.append(item)
                number += 1
            else:
                print(f"    - {item.path}  ({format_size(item.size)}; {item.reason})")
    if not inventory.instance_found:
        print("\nThe run did not report a Floability instance; only command logs are shown.")
    if inventory.truncated:
        print("\nWarning: the remote file list was limited to 2,000 files.")
    return selectable


def select_file_interactively(files: List):
    import sys

    if not files:
        raise RemoteRunError("This run has no files within the download limit.")
    if not sys.stdin.isatty():
        raise RemoteRunError("Interactive selection requires a terminal; use --file with a listed path.")
    answer = input(f"\nSelect a file to download [1-{len(files)}]: ").strip()
    try:
        index = int(answer)
    except ValueError as error:
        raise RemoteRunError("File selection must be a number.") from error
    if not 1 <= index <= len(files):
        raise RemoteRunError(f"File selection must be between 1 and {len(files)}.")
    return files[index - 1]


def _clock(timestamp: Optional[float]) -> str:
    return time.strftime("%H:%M:%S", time.localtime(timestamp)) if timestamp else "-"


def print_taskvine_status(status: TaskVineStatus) -> None:
    if not status.instance_found:
        print("Floability has not created its instance yet.")
        return
    if not status.uses_taskvine:
        print("No TaskVine manager has started in this run; the workflow may not use TaskVine.")
        return
    if status.manager_state == "running":
        manager = f"running since {_clock(status.manager_started_at)}"
    elif status.manager_state == "ended":
        manager = f"stopped at {_clock(status.manager_ended_at)}"
    else:
        manager = "not started yet"
    print(f"Manager   {manager}")
    workers = f"{status.workers_connected} connected"
    if status.manager_state == "ended":
        workers = f"{status.workers_seen} took part in the run"
    elif status.factory:
        workers += (
            f" · {status.factory.submitted} submitted to the batch system,"
            f" {status.factory.waiting_connection} waiting to connect"
        )
    print(f"Workers   {workers}")
    print(
        f"Tasks     {status.tasks_waiting} waiting · {status.tasks_running} running"
        f" · {status.tasks_done} done · {status.tasks_failed} failed"
    )
    if status.recent:
        print("Recent")
        for event in status.recent:
            print(f"  {_clock(event.time)}  {event.text}")
