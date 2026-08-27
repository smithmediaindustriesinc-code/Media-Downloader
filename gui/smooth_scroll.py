"""
Smooth (animated) scrolling, applied to every CTkScrollableFrame in the
app - the settings panel, request history, playlists list, version tab,
history tab, dialogs, everywhere.

customtkinter moves a scrollable frame's canvas by calling
canvas.yview_scroll(N, "units") - by default this happens all at once for
however many units one mouse-wheel notch is worth, which reads as a
series of small jumps rather than smooth motion. patch_smooth_scrolling()
intercepts that call at the canvas level (not the event bindings, which
would mean re-implementing customtkinter's own wheel-event routing and
risk breaking it) and spreads a single "scroll by N units" request across
several quick animation ticks instead - the same eventual scroll amount,
delivered as a glide instead of a jump.

This is applied automatically to EVERY CTkScrollableFrame the app
creates, anywhere, by patching the class itself once at import time -
individual call sites in app.py/dialogs.py/request_history.py don't need
to do anything differently.
"""
import customtkinter as ctk

_STEP_MS = 8       # time between animation ticks - lower = faster
                    # scrolling. Module-level and read fresh by animate()
                    # on every tick (not captured into a per-canvas
                    # closure at creation time), so changing it via
                    # set_scroll_speed() below affects every scrollable
                    # frame already on screen immediately, not just ones
                    # created after the change - no re-patching needed.
_MAX_STEPS_AT_ONCE = 12  # caps a single very-large scroll request from
                          # taking an oddly long time to finish animating

_patched = False


def set_scroll_speed(step_ms):
    """The Accessibility setting's actual effect - a lower step_ms means
    less delay between each animation tick, i.e. faster-feeling scroll;
    a higher one means slower, more gradual scrolling. Clamped to a
    sane range (2-40ms) so an extreme value can't make scrolling either
    imperceptibly instant or unusably sluggish."""
    global _STEP_MS
    _STEP_MS = max(2, min(40, int(step_ms)))


def _enable_smooth_scroll(frame):
    canvas = getattr(frame, "_parent_canvas", None)
    if canvas is None or getattr(canvas, "_smooth_scroll_patched", False):
        return
    original_yview_scroll = canvas.yview_scroll

    state = {"pending": 0, "running": False}

    def animate():
        if state["pending"] == 0:
            state["running"] = False
            return
        step = 1 if state["pending"] > 0 else -1
        try:
            original_yview_scroll(step, "units")
        except Exception:
            state["pending"] = 0
            state["running"] = False
            return
        state["pending"] -= step
        canvas.after(_STEP_MS, animate)

    def patched_yview_scroll(number, what, *args):
        if what != "units":
            return original_yview_scroll(number, what, *args)
        try:
            n = int(number)
        except (TypeError, ValueError):
            return original_yview_scroll(number, what, *args)
        n = max(-_MAX_STEPS_AT_ONCE, min(_MAX_STEPS_AT_ONCE, n))
        state["pending"] += n
        if not state["running"]:
            state["running"] = True
            animate()

    canvas.yview_scroll = patched_yview_scroll
    canvas._smooth_scroll_patched = True


def patch_smooth_scrolling():
    """Call once, early at app startup. Wraps CTkScrollableFrame.__init__
    so every instance created anywhere in the app - now and in the
    future, no per-call-site changes needed - gets smooth scrolling
    automatically."""
    global _patched
    if _patched:
        return
    _patched = True

    original_init = ctk.CTkScrollableFrame.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            _enable_smooth_scroll(self)
        except Exception:
            pass  # smoother scrolling is a nicety, never worth breaking a screen over

    ctk.CTkScrollableFrame.__init__ = patched_init
