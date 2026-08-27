"""
A collapsible-sidebar navigation widget used in place of customtkinter's
CTkTabview for the app's main navigation. CTkTabview's horizontal
segmented-button strip doesn't support a hide/reveal sidebar layout, so
this is a small drop-in-ish replacement instead - it implements just
enough of CTkTabview's public surface (.add(name), .set(name), .get(),
.delete(name), an optional on-change `command`) that the rest of the app
didn't need rewriting to use it; only the construction site and the one
place that reached into CTkTabview's private internals for the Settings
gear icon needed to change.

Layout: a fixed-width sidebar of vertical buttons (one per tab, in the
order .add() was called) next to a content area where each tab's frame
occupies the same grid cell and switching tabs just raises the target
frame to the top (tkraise()) - cheap, and avoids re-building tab content
on every switch. The sidebar can be collapsed to just its toggle button,
handing the freed width back to the content area automatically (it sits
in a weighted grid column, so shrinking the sidebar's fixed-width column
directly grows it).
"""
import customtkinter as ctk


class SidebarTabview(ctk.CTkFrame):
    def __init__(self, master, command=None, sidebar_width=170, loading_delay_provider=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.command = command
        self._sidebar_width = sidebar_width
        self._collapsed = False
        # Optional callable -> (enabled: bool, delay_ms: int) - see
        # _maybe_show_loading_overlay(). None (the default) or returning
        # (False, ...) means set() behaves EXACTLY as before, with zero
        # risk to anything relying on it being synchronous/immediate -
        # this is purely an additive, opt-in visual smoothing effect,
        # never a real delay to when the tab actually switches
        # underneath (get()/_current update immediately either way).
        self.loading_delay_provider = loading_delay_provider
        self._loading_overlay = None

        self._tabs = {}          # name -> content frame
        self.buttons_dict = {}   # name -> sidebar CTkButton (public, unlike CTkTabview's private one)
        self._order = []
        self._current = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=sidebar_width)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)

        self.toggle_btn = ctk.CTkButton(self.sidebar, text="\u2630", width=32, height=28,
                                         font=ctk.CTkFont(size=16), command=self.toggle_sidebar,
                                         fg_color="transparent", hover_color=("gray80", "gray25"),
                                         text_color=("gray20", "gray85"))
        # Right-anchored (not left) so it hugs the right edge of the
        # sidebar column regardless of the sidebar's current width -
        # when collapsed, the button visually "follows" the sidebar in
        # rather than staying pinned to the far-left edge of the window.
        self.toggle_btn.pack(anchor="e", padx=8, pady=(10, 12))

        self.button_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        # Version's own frame, anchored to the BOTTOM of the sidebar
        # (packed with side="bottom" BEFORE button_frame claims the rest
        # of the space) - genuinely at the very bottom with whatever
        # space is left between it and the main group, not just sitting
        # last in one shared top-down list with a bit of extra padding
        # (which was the previous, weaker attempt at this).
        self.version_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.version_frame.pack(side="bottom", fill="x", padx=6, pady=(2, 8))
        self.button_frame.pack(fill="both", expand=True, padx=6)

        self.content_area = ctk.CTkFrame(self, fg_color="transparent")
        self.content_area.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        self.content_area.grid_columnconfigure(0, weight=1)
        self.content_area.grid_rowconfigure(0, weight=1)

    # ------------------------------------------------------------------ #
    def add(self, name, index=None):
        """Matches CTkTabview.add(name): creates and returns a content
        frame for a new tab. index, if given, inserts it at that position
        in the sidebar instead of appending at the end - used for the
        Developer tab, which needs to land between More and Version in
        the sidebar even though it's only ever added well after both
        (on a successful dev login, long after startup)."""
        frame = ctk.CTkFrame(self.content_area, fg_color="transparent")
        frame.grid(row=0, column=0, sticky="nsew")
        self._tabs[name] = frame

        if index is None or index >= len(self._order):
            self._order.append(name)
        else:
            self._order.insert(index, name)

        btn = ctk.CTkButton(self.version_frame if name == "Version" else self.button_frame,
                             text=name.strip(), anchor="w", font=ctk.CTkFont(size=13),
                             fg_color="transparent", hover_color=("gray80", "gray25"),
                             command=lambda n=name: self.set(n))
        btn._sidebar_full_text = name.strip()  # remembered so collapsing/expanding can restore it exactly
        self.buttons_dict[name] = btn
        self._repack_buttons()
        if self._collapsed:
            self._apply_collapsed_style(btn)

        if self._current is None:
            self.set(name)
        return frame

    def _repack_buttons(self):
        """Re-lays-out the sidebar buttons to match self._order exactly -
        needed because pack() only ever appends visually, so inserting a
        button at a specific list position (see `index` in add()) still
        needs every button re-packed in the correct final order to
        actually show up in the right place. Version lives in its own
        separate version_frame (anchored to the bottom of the sidebar -
        see __init__), so it's excluded here and never needs reordering
        among the others - it's genuinely alone at the very bottom of
        the frame, not just last in this same top-down list."""
        for name in self._order:
            if name != "Version":
                self.buttons_dict[name].pack_forget()
        for name in self._order:
            if name != "Version":
                self.buttons_dict[name].pack(fill="x", pady=(2, 2))
        if "Version" in self.buttons_dict:
            self.buttons_dict["Version"].pack(fill="x")

    def set(self, name):
        """Matches CTkTabview.set(name): switches the active tab. The
        switch itself (self._current, tkraise()) is always immediate and
        synchronous - only the optional loading overlay (see
        _maybe_show_loading_overlay) is ever delayed, purely visual,
        never gating when the tab actually becomes current."""
        if name not in self._tabs:
            return
        self._current = name
        self._tabs[name].tkraise()
        for n, btn in self.buttons_dict.items():
            btn.configure(fg_color=("gray75", "gray28") if n == name else "transparent")
        self._maybe_show_loading_overlay()
        if self.command:
            self.command()

    # Rotating quarter-circle glyphs - matches the same simple, circular,
    # gray line-art language the sidebar's own icons use (see the More
    # icon specifically, redrawn as a genuine full circle) - just bigger
    # and animated, per how this was specifically asked for.
    _SPINNER_FRAMES = ["\u25d0", "\u25d3", "\u25d1", "\u25d2"]

    def _maybe_show_loading_overlay(self):
        """A brief, neutral overlay covering the content area for a
        configured minimum duration on each tab switch - purely a visual
        smoothing effect ("minimize what the user sees" during a
        switch), never an actual delay to the underlying tab change,
        which has already happened by the time this is called. Off by
        default (loading_delay_provider is None, or returns
        (False, ...)) - a no-op in that case, so nothing about normal
        tab-switching behavior changes unless this is deliberately
        turned on.

        Layering, bottom to top, per how this was specifically asked
        for: the actual (already fully switched) GUI underneath -> a
        blank frame the exact size of the content area, raised on top
        of it via tkraise() -> a low-key rotating spinner glyph
        centered on top of THAT, styled like the sidebar's own icons.
        Once the configured duration elapses, the spinner's animation
        loop stops itself (its label no longer exists to reconfigure)
        and the blank frame is destroyed, revealing the real, already-
        complete GUI underneath - nothing was ever hidden except
        visually, and nothing needed to be rebuilt or re-rendered."""
        if not self.loading_delay_provider:
            return
        try:
            enabled, delay_ms = self.loading_delay_provider()
        except Exception:
            return
        if not enabled or not delay_ms:
            return
        if self._loading_overlay is not None and self._loading_overlay.winfo_exists():
            self._loading_overlay.destroy()
        overlay = ctk.CTkFrame(self.content_area, fg_color=("gray90", "gray14"))
        overlay.grid(row=0, column=0, sticky="nsew")
        overlay.tkraise()
        spinner_label = ctk.CTkLabel(overlay, text=self._SPINNER_FRAMES[0], font=ctk.CTkFont(size=40),
                                      text_color=("gray60", "gray45"))
        spinner_label.place(relx=0.5, rely=0.5, anchor="center")
        self._loading_overlay = overlay
        self._animate_spinner(spinner_label, 0)
        self.after(int(delay_ms), lambda o=overlay: self._hide_loading_overlay(o))

    def _animate_spinner(self, label, frame_index):
        """Cycles through _SPINNER_FRAMES on a short interval - stops
        itself automatically once the label is gone (the overlay it
        belongs to was destroyed), rather than needing an explicit
        cancel/stop call or leaking a runaway .after() chain."""
        if not label.winfo_exists():
            return
        label.configure(text=self._SPINNER_FRAMES[frame_index % len(self._SPINNER_FRAMES)])
        self.after(150, lambda: self._animate_spinner(label, frame_index + 1))

    def _hide_loading_overlay(self, overlay):
        if overlay.winfo_exists():
            overlay.destroy()
        if self._loading_overlay is overlay:
            self._loading_overlay = None

    def get(self):
        """Matches CTkTabview.get(): name of the currently active tab."""
        return self._current

    def tab(self, name):
        """Matches CTkTabview.tab(name): the content frame for that tab."""
        return self._tabs.get(name)

    def delete(self, name):
        """Matches CTkTabview.delete(name): removes a tab entirely (used
        for the Developer tab, which only exists after a dev login and
        is removed again on logout)."""
        if name in self._tabs:
            self._tabs[name].destroy()
            del self._tabs[name]
        if name in self.buttons_dict:
            self.buttons_dict[name].destroy()
            del self.buttons_dict[name]
        if name in self._order:
            self._order.remove(name)
        if self._current == name:
            self._current = self._order[0] if self._order else None
            if self._current:
                self.set(self._current)

    # ------------------------------------------------------------------ #
    def _apply_collapsed_style(self, btn):
        """Icon-only: text hidden, centered (rather than left-anchored,
        which would leave the icon awkwardly offset with no label text
        next to it), narrower. The button itself stays fully packed and
        clickable the whole time - see toggle_sidebar()."""
        btn.configure(text="", anchor="center", width=32)

    def _apply_expanded_style(self, btn):
        btn.configure(text=getattr(btn, "_sidebar_full_text", ""), anchor="w", width=140)

    def toggle_sidebar(self):
        """Collapses the sidebar to a narrow, icon-only strip, or
        restores full labels - the content area automatically gets the
        freed width back (or gives it up again) since it's the weighted
        column in this widget's own grid.

        Follows a specific order, per how this was asked for: 1) re-
        render the tab buttons as icon-only first (getting rid of the
        text), 2) THEN shrink the sidebar frame itself, 3) THEN force a
        single clean re-render of the whole window
        (update_idletasks()) - restyling the buttons before resizing
        their container means Tk never has to lay out oversized text
        labels inside an already-narrow frame mid-transition, which is
        what a shrink-first order could momentarily do.

        Every button stays packed and clickable throughout - this used
        to call pack_forget() on the whole button_frame when collapsing,
        which hid every tab button entirely (not just their text),
        directly working against "keep the tab buttons visible and
        operational" while collapsed. Only each button's own text/width
        changes now, never its presence - a much smaller, cleaner state
        change that also avoids the pack/re-pack reflow that was
        causing visible artifacts during the transition."""
        self._collapsed = not self._collapsed
        if self._collapsed:
            for btn in self.buttons_dict.values():
                self._apply_collapsed_style(btn)
            self.sidebar.configure(width=44)
        else:
            for btn in self.buttons_dict.values():
                self._apply_expanded_style(btn)
            self.sidebar.configure(width=self._sidebar_width)
        self.update_idletasks()

    def is_collapsed(self):
        return self._collapsed
