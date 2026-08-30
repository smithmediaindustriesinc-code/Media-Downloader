"""One place every error in the app is recorded: <app_dir>/error_log.txt
(created on first write). Used by the Tk exception hook, the crash logger,
and any 'except Exception' site that would otherwise swallow the error.
Never raises."""
import os
import traceback
import datetime
from core.paths import app_dir


def _path():
    return os.path.join(app_dir(), "error_log.txt")


def log_error(where, exc=None, extra=""):
    """Append a timestamped entry. `where` = short context string;
    `exc` = an exception instance (its traceback is included if available);
    `extra` = any extra detail. Best-effort - swallows its own failures."""
    try:
        lines = [f"----- {datetime.datetime.now().isoformat()}  [{where}] -----"]
        if extra:
            lines.append(str(extra))
        if exc is not None:
            lines.append("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip())
        text = "\n".join(lines) + "\n\n"
        p = _path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def log_message(where, message):
    """A non-exception note (a handled failure, a warning worth keeping)."""
    log_error(where, exc=None, extra=message)
