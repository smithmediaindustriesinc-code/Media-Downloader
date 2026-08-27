"""A dead-simple heartbeat log for diagnosing crashes that happen below
Python's own exception handling (native/Tcl-level crashes, DLL issues,
etc) - the kind that leave no traceback and no crash_log.txt entry.

Each stage of startup calls mark("stage name"), which appends a line and
flushes immediately. If the app dies with zero Python exception, whatever
the LAST line in this file is tells us exactly which stage it never got
past - which is enough to pinpoint the cause even with no traceback."""
import os
import datetime

from core.paths import app_dir

STARTUP_LOG_PATH = os.path.join(app_dir(), "startup_log.txt")


def mark(stage, durable=True):
    try:
        with open(STARTUP_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat()}  {stage}\n")
            f.flush()
            if durable:
                os.fsync(f.fileno())
    except Exception:
        pass  # never let logging itself be a new crash source


def reset():
    try:
        with open(STARTUP_LOG_PATH, "w", encoding="utf-8") as f:
            f.write(f"--- new run: {datetime.datetime.now().isoformat()} ---\n")
    except Exception:
        pass
