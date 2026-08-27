"""Catches anything that would otherwise crash the app silently (no console,
no error shown) and writes it to a plain-text log next to the .exe/script,
plus shows a message box so the user isn't left staring at nothing."""
import os
import sys
import traceback
import datetime

from core.paths import app_dir

CRASH_LOG_PATH = os.path.join(app_dir(), "crash_log.txt")


def log_error(text):
    """Public entry point for appending arbitrary error text to
    crash_log.txt - used by anything that catches its own exceptions but
    still wants them recorded (e.g. the recurring-task scheduler, which
    must never let one bad tick kill a loop, but still wants a record)."""
    _write(text)


def _write(text):
    try:
        with open(CRASH_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n----- {datetime.datetime.now().isoformat()} -----\n")
            f.write(text)
            f.write("\n")
    except Exception:
        pass  # even logging failed - nothing more we can do


def install_global_excepthook():
    """Catches exceptions that happen outside any tkinter callback (e.g.
    during App.__init__ before mainloop starts)."""
    def hook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _write(text)
        try:
            import tkinter.messagebox as mb
            mb.showerror(
                "Media Downloader crashed",
                "The app hit an error on startup and had to close.\n\n"
                f"Details were saved to:\n{CRASH_LOG_PATH}\n\n"
                "Please share that file so this can be fixed.\n\n"
                f"{exc_type.__name__}: {exc_value}"
            )
        except Exception:
            print(text)
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    sys.excepthook = hook


def install_tk_report_callback(app):
    """Catches exceptions raised inside tkinter callbacks (button clicks,
    variable traces, .after() calls) - these are normally swallowed/printed
    to a console that may not exist, which is exactly what looked like the
    app silently vanishing."""
    def report(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _write(text)
        try:
            import tkinter.messagebox as mb
            mb.showerror(
                "Something went wrong",
                f"An action failed:\n\n{exc_type.__name__}: {exc_value}\n\n"
                f"Details were saved to:\n{CRASH_LOG_PATH}"
            )
        except Exception:
            print(text)
    app.report_callback_exception = report
