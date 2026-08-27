"""
Modal error popup + Windows system beep, for anything that should
interrupt the user rather than just get logged.

winsound is Python's own stdlib module (Windows-only, ships with every
Windows Python install - no pip install needed), so MessageBeep is the
simplest and most efficient way to do this: no new dependency, no extra
audio file to bundle, and it plays whatever sound the user's own Windows
sound scheme has assigned to that system event, which is exactly what
"the default Windows sound" means.
"""
import platform
from tkinter import messagebox

if platform.system() == "Windows":
    import winsound

    def _beep():
        try:
            # MB_ICONHAND = the "system error" sound in the user's own
            # Windows sound scheme - the standard OS-level error beep.
            winsound.MessageBeep(winsound.MB_ICONHAND)
        except Exception:
            pass
else:
    def _beep():
        pass  # winsound is Windows-only; silently skip elsewhere


def show_error(title, message, parent=None):
    """Plays the OS error beep and shows a modal error dialog. Use this
    (instead of a plain messagebox.showerror) for anything that should
    actively interrupt the user - a download that failed after all
    retries, a missing dependency blocking an action, etc."""
    _beep()
    if parent is not None:
        messagebox.showerror(title, message, parent=parent)
    else:
        messagebox.showerror(title, message)
