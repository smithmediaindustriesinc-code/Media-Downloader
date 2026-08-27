"""
Small app-wide widget behavior patches applied once at startup, before
any CTk widgets are constructed - so every instance (now and any created
later) picks these up automatically, without touching each call site.
"""
import sys
import customtkinter as ctk


def _find_scrollable_ancestor(widget):
    """Walks up the widget tree looking for a CTkScrollableFrame's own
    canvas (identifiable by having a _parent_canvas attribute on some
    ancestor, or being one itself) - same lookup pattern used in
    gui/smooth_scroll.py."""
    w = widget
    depth = 0
    while w is not None and depth < 12:
        canvas = getattr(w, "_parent_canvas", None)
        if canvas is not None:
            return canvas
        w = getattr(w, "master", None)
        depth += 1
    return None


def disable_slider_mousewheel():
    """A slider shouldn't change value from a stray scroll of the mouse
    wheel while hovering over it - but simply turning the handler into a
    no-op (the original approach) has a real side effect: CTkSlider
    binds <MouseWheel> directly on its own canvas, and a bound handler -
    even one that does nothing - still CONSUMES the event there, so it
    never propagates up to whatever CTkScrollableFrame the slider sits
    inside. That's exactly why scrolling over a slider stopped the whole
    page from scrolling instead of just not moving the slider.

    The actual fix: redirect the event to the nearest scrollable
    ancestor's own canvas instead of swallowing it, so scrolling over a
    slider scrolls the page around it, which is what a user actually
    wants in that moment."""
    def redirect_to_page_scroll(self, event):
        canvas = _find_scrollable_ancestor(self)
        if canvas is None:
            return
        if sys.platform.startswith("win"):
            direction = -int(event.delta / 120)
        elif sys.platform == "darwin":
            direction = -int(event.delta)
        else:
            direction = -1 if getattr(event, "num", 5) == 4 else 1
        try:
            canvas.yview_scroll(direction, "units")
        except Exception:
            pass  # a failed scroll redirect is never worth raising over

    ctk.CTkSlider._mouse_scroll_event = redirect_to_page_scroll
