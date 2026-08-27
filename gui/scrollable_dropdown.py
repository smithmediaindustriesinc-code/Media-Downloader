"""A dropdown button that opens a scrollable popup list instead of the stock
CTkOptionMenu menu (which doesn't scroll for long option lists like the
expanded font/quality lists in this app)."""
import customtkinter as ctk


class ScrollableDropdown(ctk.CTkFrame):
    # Tracks every instance with an open popup, so the app can close them
    # all at once (e.g. when switching tabs - a dropdown left open on the
    # tab you're leaving would otherwise visually float on top of
    # whatever tab you switch to, since it's a separate Toplevel window
    # that doesn't belong to any one tab's frame).
    _open_instances = []

    def __init__(self, master, values, variable, font=None, command=None,
                 width=220, max_visible=8, display_map=None, **kwargs):
        """display_map, if given, maps an actual stored value -> the label
        shown to the user (e.g. {"blue": "Blue"}). The variable always
        holds the real value regardless of what's displayed - this is
        purely a presentation layer, for cases like color theme names
        where the on-disk/internal identifier ("blue") needs to stay
        lowercase for file lookups, but the user should see "Blue"."""
        super().__init__(master, fg_color="transparent", **kwargs)
        self.values = values
        self.variable = variable
        self.command = command
        self.font = font
        self.max_visible = max_visible
        self.display_map = display_map or {}
        self._popup = None

        self.button = ctk.CTkButton(
            self, text=self._label_for(self.variable.get()) or (self._label_for(values[0]) if values else ""),
            font=font, width=width, anchor="w", command=self._toggle
        )
        self.button.pack(fill="x")

        self.variable.trace_add("write", self._on_var_change)

    def _label_for(self, value):
        return self.display_map.get(value, value)

    def _on_var_change(self, *_):
        self.button.configure(text=self._label_for(self.variable.get()))

    def _toggle(self):
        if self._popup and self._popup.winfo_exists():
            self._close_popup()
            return
        self._open_popup()

    def _open_popup(self):
        self._popup = ctk.CTkToplevel(self)
        self._popup.overrideredirect(True)
        self._popup.attributes("-topmost", True)

        x = self.button.winfo_rootx()
        y = self.button.winfo_rooty() + self.button.winfo_height()
        width = self.button.winfo_width()
        # Was 30px, which clipped text vertically at larger font sizes
        # (this app's Settings lets users go well past the default font
        # size) - each option row is now built with an explicit taller
        # height to match, so the popup's own sizing math and the actual
        # rendered row height always agree.
        row_h = 34
        visible = min(len(self.values), self.max_visible)
        height = max(row_h, row_h * visible)
        self._popup.geometry(f"{width}x{height}+{x}+{y}")

        scroll = ctk.CTkScrollableFrame(self._popup, fg_color=("gray90", "gray17"),
                                         width=width, height=height)
        scroll.pack(fill="both", expand=True)

        for val in self.values:
            ctk.CTkButton(
                scroll, text=str(self._label_for(val)), font=self.font, anchor="w", height=row_h - 4,
                fg_color="transparent", hover_color=("gray80", "gray25"),
                command=lambda v=val: self._select(v)
            ).pack(fill="x", pady=1)

        # NOTE: this used to also bind <FocusOut> on the popup to
        # auto-close it when clicking elsewhere. That was the actual bug
        # behind "dropdown selections don't stick": clicking an option
        # button inside the popup transfers keyboard focus from the
        # popup Toplevel to that button, which fires <FocusOut> on the
        # Toplevel *before* the button's own click/command has finished
        # processing - so the popup (and the button being clicked) got
        # destroyed mid-click, cancelling _select() before it ran. The
        # value only ever silently reverted to whatever it already was.
        # Dismissing without picking anything still works fine: just
        # click the dropdown button again to close it (_toggle above).
        if self not in ScrollableDropdown._open_instances:
            ScrollableDropdown._open_instances.append(self)

    def _close_popup(self):
        if self._popup and self._popup.winfo_exists():
            self._popup.destroy()
        self._popup = None
        if self in ScrollableDropdown._open_instances:
            ScrollableDropdown._open_instances.remove(self)

    @classmethod
    def close_all(cls):
        """Closes every currently-open dropdown popup, anywhere in the
        app - called on every tab switch so a popup left open on the tab
        being left doesn't visually float on top of the tab being
        switched to (it's a separate Toplevel window, not a child of any
        one tab's frame, so it otherwise stays put regardless of what
        tab is showing)."""
        for instance in list(cls._open_instances):
            instance._close_popup()

    def _select(self, value):
        self.variable.set(value)
        self._close_popup()
        if self.command:
            self.command(value)

    def configure_values(self, values, display_map=None):
        self.values = values
        if display_map is not None:
            self.display_map = display_map

    def get(self):
        return self.variable.get()

