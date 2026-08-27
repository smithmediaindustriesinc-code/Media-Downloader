"""
The boot splash: a minimum-5-second (configurable up to a max before the
text switches to "please wait...") animated loading screen shown before
the real app window - "Launching..." with an animated ellipsis, a
progress bar, and the app's own actual logo (assets/media_center.png -
the same source the real icon.ico is built from) easing in: small,
faded and slightly sunken, growing to full size and opacity as the
splash plays. This used to be a hand-drawn, PIL-redrawn approximation
of the real icon (a separately-coded ring/arrow/filmstrip/note/photo
built from scratch); using the actual artwork instead means the splash
can never drift out of sync with what the real icon looks like again.

Honest limitation: Tkinter is single-threaded, so this can't animate
smoothly WHILE the real App() is doing its own (normally fast, well
under a second) synchronous construction work - the animation runs
before App() is constructed, not literally during it (see run() in
gui/app.py for why: constructing App() while the splash's separate Tk
root was still alive was actually the root cause of the sidebar tab
icons silently failing to appear, so the two no longer overlap at all
now). The minimum-duration and "please wait" text-switch logic are
real and tested.
"""
import os
import time

import customtkinter as ctk
from PIL import Image

MIN_DISPLAY_SECONDS = 5
PLEASE_WAIT_AFTER_SECONDS = 60
_REVEAL_SECONDS = 1.2  # how long the grow-and-fade-in of the real icon takes
_HOLD_SECONDS = 0.35   # brief pause at each end of the pulse before it reverses
BG_COLOR = (10, 14, 30)  # matches the icon artwork's own dark backdrop, not a separately-tuned color

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
_LOGO_SOURCE = None  # lazily-loaded, full-res original icon artwork (see _logo_source())


def _logo_source():
    """The real icon artwork (assets/media_center.png), loaded once and
    cached at module level. Falls back to icon.ico if the source PNG
    isn't where expected (e.g. a stripped-down dev checkout) - either
    way this is the ACTUAL icon, never a redrawn stand-in."""
    global _LOGO_SOURCE
    if _LOGO_SOURCE is None:
        png_path = os.path.join(_ASSETS_DIR, "media_center.png")
        ico_path = os.path.join(os.path.dirname(_ASSETS_DIR), "icon.ico")
        path = png_path if os.path.exists(png_path) else ico_path
        _LOGO_SOURCE = Image.open(path).convert("RGBA")
    return _LOGO_SOURCE


def _ease_out_back(t):
    """A slight overshoot-then-settle easing (gentle version of the
    classic "back" ease) - the logo grows a hair past full size before
    settling, which reads as a soft, confident "pop" into place rather
    than just linearly growing, without needing any extra drawing code
    of its own."""
    c1 = 1.70158
    c3 = c1 + 1
    t = min(1.0, max(0.0, t))
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def _draw_logo_frame(elapsed_seconds):
    """elapsed_seconds: real time since the splash started. Maps onto an
    oscillating grow+fade reveal of the real icon artwork: it grows in
    over _REVEAL_SECONDS, holds briefly, shrinks back out, holds briefly,
    then loops - a slow breathing pulse for as long as the splash stays
    up. Returns a PIL Image (RGBA, 256x256) for this exact moment -
    called repeatedly with an increasing elapsed_seconds to animate."""
    size = 256
    source = _logo_source()

    # Oscillating reveal: grow in over _REVEAL_SECONDS, hold at full size for
    # _HOLD_SECONDS, shrink back out over _REVEAL_SECONDS, hold empty, then
    # loop - for as long as the splash stays up.
    _span = _REVEAL_SECONDS + _HOLD_SECONDS
    _cycle = 2 * _span
    _phase = max(0.0, elapsed_seconds) % _cycle
    if _phase < _REVEAL_SECONDS:
        t = _phase / _REVEAL_SECONDS
    elif _phase < _span:
        t = 1.0
    elif _phase < _span + _REVEAL_SECONDS:
        t = 1.0 - (_phase - _span) / _REVEAL_SECONDS
    else:
        t = 0.0
    t = min(1.0, max(0.0, t))
    alpha = int(255 * min(1.0, t * 1.3))  # fades in slightly faster than it finishes growing
    scale = 0.55 + 0.45 * _ease_out_back(t)

    frame = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if alpha <= 0:
        return frame

    logo_size = max(1, int(size * 0.86 * scale))
    logo = source.resize((logo_size, logo_size), Image.LANCZOS)
    if alpha < 255:
        r, g, b, a = logo.split()
        a = a.point(lambda v: v * alpha // 255)
        logo = Image.merge("RGBA", (r, g, b, a))

    pos = ((size - logo_size) // 2, (size - logo_size) // 2)
    frame.alpha_composite(logo, pos)
    return frame


class SplashScreen(ctk.CTk):
    """A standalone splash window - constructed and shown BEFORE the
    real App(), since there's nothing else to attach a Toplevel to yet
    at that point in startup."""

    def __init__(self):
        super().__init__()
        self.overrideredirect(True)
        self.geometry("340x420+{}+{}".format(
            (self.winfo_screenwidth() - 340) // 2, (self.winfo_screenheight() - 420) // 2))
        self.configure(fg_color=(f"#{BG_COLOR[0]:02x}{BG_COLOR[1]:02x}{BG_COLOR[2]:02x}"))

        self.logo_label = ctk.CTkLabel(self, text="", width=256, height=256)
        self.logo_label.pack(pady=(30, 10))

        self.status_label = ctk.CTkLabel(self, text="Launching", font=ctk.CTkFont(size=16, weight="bold"))
        self.status_label.pack(pady=(0, 10))

        self.progress_bar = ctk.CTkProgressBar(self, width=260, mode="indeterminate")
        self.progress_bar.pack(pady=(0, 20))
        self.progress_bar.start()

        self._start_time = time.time()
        self._dot_count = 0
        self._logo_photo = None  # kept alive - see the app-wide CTkImage GC note elsewhere in this codebase
        self._closed = False

    def animate_tick(self):
        """One frame of the whole splash: redraws the logo at its
        current reveal stage, cycles the "Launching..." ellipsis, and
        switches to "please wait" text once PLEASE_WAIT_AFTER_SECONDS
        has passed. Caller is responsible for re-scheduling this (kept
        as a plain method, not self-scheduling via .after(), so a test
        can call it directly without needing a real timer)."""
        if self._closed or not self.winfo_exists():
            return
        frame = _draw_logo_frame(self.elapsed_seconds())
        self._logo_photo = ctk.CTkImage(light_image=frame, dark_image=frame, size=(256, 256))
        self.logo_label.configure(image=self._logo_photo, text="")

        elapsed = time.time() - self._start_time
        if elapsed >= PLEASE_WAIT_AFTER_SECONDS:
            self.status_label.configure(text="Please wait...")
        else:
            self._dot_count = (self._dot_count + 1) % 4
            self.status_label.configure(text="Launching" + "." * self._dot_count)

    def elapsed_seconds(self):
        return time.time() - self._start_time

    def close(self):
        self._closed = True
        if self.winfo_exists():
            # CTkProgressBar's indeterminate mode (.start()) runs its
            # own internal .after()-based animation loop - explicitly
            # .stop()ping it before destroy() is the correct, documented
            # way to clean that up. A first attempt at this tried a
            # blanket "cancel every pending after() callback" approach
            # instead, which seemed reasonable but actually broke
            # something worse: it interfered with the progress bar's
            # own internal Tcl command bookkeeping, causing destroy()
            # itself to raise "can't delete Tcl command" - a genuine
            # regression caught by testing, not a hypothetical. This
            # targeted fix (stop the one thing that actually needs
            # stopping) avoids both problems.
            try:
                self.progress_bar.stop()
            except Exception:
                pass
            # customtkinter's own CTk base class ALSO schedules a couple
            # of its own internal periodic .after() loops (an "update"
            # loop and a "check_dpi_scaling" loop) that aren't tied to
            # any one widget the way the progress bar's was - there's no
            # single documented "stop" call for those. destroy() itself
            # can raise partway through if one of those fires at just
            # the wrong moment during teardown - and because close() is
            # called synchronously here (not dispatched through
            # mainloop()'s own callback-exception handling), that
            # exception would otherwise propagate out and interrupt
            # whatever called close(), not just print a stderr warning.
            # Swallowing a TclError specifically here (nothing else)
            # means the window still visually goes away for the user
            # either way, and callers can rely on close() always
            # completing.
            try:
                self.destroy()
            except Exception:
                pass
