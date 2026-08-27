"""
"Advanced Selecting" - the app's one reusable multi-select mechanism:
a toggle that reveals a checkbox next to each item in a list, a "Select
All" control, and bulk-action buttons that act on whatever's checked.
Built first for the URL Scraper's results list, and meant to be the
SAME mechanism reused everywhere else a list needs multi-item actions
(delete/copy/retry in History, Request History, Playlists, etc) rather
than each screen inventing its own checkbox handling - "advanced
selecting" is the deliberate internal name for this pattern, used
consistently in code comments/identifiers wherever it shows up.
"""
import customtkinter as ctk


class AdvancedSelector:
    """Wraps a list of items with optional checkbox-based multi-select.
    Doesn't render rows itself (each screen's own row-building code stays
    in charge of that) - just tracks which item ids are currently
    selected, whether selection mode is even on, and provides the
    Select All / bulk-action wiring a toolbar needs.

    Usage: one AdvancedSelector per list. Call .set_enabled(bool) from a
    toggle switch's command; check .enabled and .is_selected(item_id) while
    building each row to decide whether to show a checkbox and its
    current state; call .toggle(item_id) from a checkbox's own command;
    call .select_all(all_ids) / .clear() from toolbar buttons; call
    .selected_ids() to get the current selection for a bulk action.
    """

    def __init__(self, on_change=None):
        self.enabled = False
        self._selected = set()
        self.on_change = on_change  # called (no args) whenever enabled/selection changes, for a UI refresh

    def set_enabled(self, enabled):
        self.enabled = enabled
        if not enabled:
            self._selected.clear()
        self._notify()

    def toggle(self, item_id):
        if item_id in self._selected:
            self._selected.discard(item_id)
        else:
            self._selected.add(item_id)
        self._notify()

    def is_selected(self, item_id):
        return item_id in self._selected

    def select_all(self, all_ids):
        self._selected = set(all_ids)
        self._notify()

    def clear(self):
        self._selected.clear()
        self._notify()

    def selected_ids(self):
        return set(self._selected)

    def selected_count(self):
        return len(self._selected)

    def _notify(self):
        if self.on_change:
            self.on_change()


def build_selection_toolbar(parent, selector, all_ids_getter, on_download=None, on_copy=None,
                             on_delete=None, download_label="Download Selected", copy_label="Copy Selected",
                             delete_label="Delete Selected", font_normal=None, font_small=None):
    """Builds the standard advanced-selecting toolbar: the enable toggle,
    a Select All button, a selected-count label, and whichever bulk
    action buttons the caller actually wants (a delete-heavy screen like
    History wouldn't pass on_download, for instance). Returns the
    toggle switch widget in case the caller wants to place/style it
    separately from the rest of the toolbar.

    all_ids_getter is a zero-arg callable returning the full list of
    item ids currently visible - called fresh each time Select All is
    pressed, so it always reflects the current (possibly filtered/
    searched) list rather than a stale snapshot from when the toolbar
    was built."""
    toggle_var = ctk.BooleanVar(value=selector.enabled)

    def on_toggle():
        selector.set_enabled(toggle_var.get())

    toggle = ctk.CTkSwitch(parent, text="Select multiple", font=font_normal, variable=toggle_var,
                            command=on_toggle)
    toggle.pack(side="left", padx=(0, 10))

    # width/height=1: an empty CTkFrame otherwise keeps its default 200x200,
    # which - while "Select multiple" is off (the default) and this frame has
    # no buttons in it - inflated the whole toolbar row to ~200px tall and
    # left the toggle floating in the middle of a big gap. Geometry
    # propagation is on, so it still grows to fit the buttons once shown.
    action_row = ctk.CTkFrame(parent, fg_color="transparent", width=1, height=1)
    action_row.pack(side="left")

    def refresh_visibility():
        for w in action_row.winfo_children():
            w.destroy()
        if not selector.enabled:
            return
        ctk.CTkButton(action_row, text="Select All", width=90, font=font_small,
                      command=lambda: selector.select_all(all_ids_getter())).pack(side="left", padx=(0, 6))
        ctk.CTkButton(action_row, text="Clear", width=70, font=font_small, fg_color="gray40",
                      hover_color="gray30", command=selector.clear).pack(side="left", padx=(0, 10))
        count_label = ctk.CTkLabel(action_row, text=f"{selector.selected_count()} selected",
                                    font=font_small, text_color="gray60")
        count_label.pack(side="left", padx=(0, 10))
        if on_download:
            ctk.CTkButton(action_row, text=download_label, font=font_small,
                          command=on_download).pack(side="left", padx=(0, 6))
        if on_copy:
            ctk.CTkButton(action_row, text=copy_label, font=font_small, fg_color="gray40",
                          hover_color="gray30", command=on_copy).pack(side="left", padx=(0, 6))
        if on_delete:
            ctk.CTkButton(action_row, text=delete_label, font=font_small, fg_color="#a13333",
                          hover_color="#7d2626", command=on_delete).pack(side="left")

    selector.on_change = refresh_visibility
    refresh_visibility()
    return toggle
