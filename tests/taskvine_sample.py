"""A sample run directory with TaskVine logs, as Floability and TaskVine write them."""

import shutil
import sys
from pathlib import Path

TRANSACTIONS = """\
# time manager_pid MANAGER manager_pid START|END time_from_origin
1791800100000000 4242 MANAGER 4242 START 0
1791800101000000 4242 TASK 1 READY default FIRST_RESOURCES 1 {}
1791800101000100 4242 TASK 2 READY default FIRST_RESOURCES 1 {}
1791800101000200 4242 TASK 3 READY default FIRST_RESOURCES 1 {}
1791800101000300 4242 TASK 4 READY default FIRST_RESOURCES 1 {}
1791800130000000 4242 WORKER worker-aa CONNECTION 10.1.2.3:40001
1791800130500000 4242 WORKER worker-aa RESOURCES {"cores":4}
1791800131000000 4242 TASK 1 RUNNING worker-aa FIRST_RESOURCES {}
1791800135000000 4242 TASK 1 WAITING_RETRIEVAL worker-aa
1791800135100000 4242 TASK 1 RETRIEVED SUCCESS 0 {}
1791800135200000 4242 TASK 1 DONE SUCCESS 0 {}
1791800136000000 4242 TASK 2 RUNNING worker-aa FIRST_RESOURCES {}
1791800138000000 4242 TASK 2 DONE SIGNAL 9 {}
1791800140000000 4242 WORKER worker-bb CONNECTION 10.1.2.4:40002
1791800141000000 4242 TASK 3 RUNNING worker-bb FIRST_RESOURCES {}
1791800150000000 4242 WORKER worker-aa DISCONNECTION IDLE_OUT
"""

FACTORY = (
    "max-workers: 4\n"
    "2026/10/12 10:15:00: |submitted: 1 |needed: 3 |waiting connection: 1 |requested: 4 \n"
    "2026/10/12 10:15:30: |submitted: 3 |needed: 1 |waiting connection: 1 |requested: 4 \n"
    "PROJECT HOST PORT\n"
)


def make_run(root: Path, *, instance=True, transactions=True, factory=True) -> Path:
    """Create <root>/run (a Floability Remote run directory) and return it."""
    run_dir = root / "run"
    instance_dir = root / "instance"
    (run_dir / "backpack").mkdir(parents=True)
    (run_dir / ".floability-remote-run").write_text("sample\n")
    lines = [f"Python executable: {shutil.which('python3') or sys.executable}\n"]
    if instance:
        instance_dir.mkdir()
        lines.append(f"[floability] Created instance structure at: {instance_dir}\n")
    (run_dir / "execute-command.log").write_text("".join(lines))
    if instance and transactions:
        logs = instance_dir / "workflow" / "vine-run-info" / "2026-10-12T101500" / "vine-logs"
        logs.mkdir(parents=True)
        (logs / "transactions").write_text(TRANSACTIONS)
    if instance and factory:
        (instance_dir / "logs").mkdir()
        (instance_dir / "logs" / "vine_factory.stdout").write_text(FACTORY)
    return run_dir
