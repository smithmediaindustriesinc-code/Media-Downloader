"""
The Request History section of the Extras tab, plus the per-request
detail dialog with retry/copy buttons. Kept in its own module since
gui/app.py is already large - these functions take the running App
instance as their first argument rather than duplicating its state.
"""
import os
import stat
import threading
import datetime
import time
import customtkinter as ctk
from tkinter import messagebox

from core.download_requests import (get_all_requests, get_request, delete_request,
                                     reopen_for_retry, update_item, finish_request, rename_request)
from core.utils import format_file_size, weighted_match_score, strip_leading_special
from gui.scrollable_dropdown import ScrollableDropdown

DISPLAY_MODES = ["Name", "Detailed", "Developer"]
SORT_MODES = ["Newest first", "Oldest first", "Alphabetical (A-Z)", "Alphabetical (Z-A)", "Largest total size"]
TYPE_FILTERS = ["All", "Video", "Audio"]

DOT_COLORS = {
    "in_progress": "#e0c020",   # yellow - still running
    "success": "#2fa84f",       # green - every item succeeded
    "partial": "#e08020",       # orange - mixed results
    "failed": "#c0392b",        # red - every item failed
}


def _dot(parent, color, size=12):
    return ctk.CTkFrame(parent, width=size, height=size, corner_radius=size // 2,
                         fg_color=color, border_width=0)


def _first_title(req):
    if req.get("custom_name"):
        return req["custom_name"]
    for item in req["items"].values():
        if item.get("name"):
            return item["name"]
    return next(iter(req["items"].keys()), "(no items)")


def _file_size_or_none(path):
    """Size of the file at `path`, or None if it isn't a regular file.
    One os.stat instead of the isfile()+getsize() pair (two stat calls)
    this used to do everywhere - the detail view renders these a lot."""
    if not path:
        return None
    try:
        st = os.stat(path)
    except (OSError, ValueError):  # ValueError: embedded NUL in path (matches genericpath.isfile)
        return None
    return st.st_size if stat.S_ISREG(st.st_mode) else None


def _total_size(req):
    """Sum of every successfully-downloaded item's file size, or None if
    nothing in the request has a real file on disk yet."""
    total = 0
    found_any = False
    for item in req["items"].values():
        size = _file_size_or_none(item.get("path"))
        if size is not None:
            total += size
            found_any = True
    return total if found_any else None


def _timestamp_str(req):
    """A human-readable timestamp for the request - finished_at once it's
    done, created_at while still in progress (there's no finished time
    yet). Used across every display mode, not just Developer, per how
    this was asked for."""
    ts = req.get("finished_at") or req.get("created_at")
    if not ts:
        return None
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


def _request_display_parts(req, mode):
    """Returns (name_text, detail_lines, dev_lines) - the name is always
    shown biggest regardless of mode; detail_lines are included for
    Detailed AND Developer mode (Developer shows everything Detailed
    would, plus its own extra info, rather than replacing it); dev_lines
    are Developer-mode-only. Rendered as visually separate, differently-
    sized labels by _build_request_row rather than one flat multi-line
    string in a single font, so the hierarchy (name biggest, everything
    else smaller and dimmer) is actually visible, not just implied by
    line order."""
    first_name = _first_title(req)
    n_items = len(req["items"])
    size_bytes = _total_size(req)
    size_str = format_file_size(size_bytes) if size_bytes is not None else None
    timestamp = _timestamp_str(req)

    name_text = first_name if n_items == 1 else f"{first_name} (+{n_items - 1} more)"
    suffix_bits = [b for b in (size_str, timestamp) if b]
    if suffix_bits and mode == "Name":
        name_text += " - " + " - ".join(suffix_bits)

    detail_lines = []
    if mode in ("Detailed", "Developer"):
        first_url = next(iter(req["items"].keys()), "")
        first_path = next((i.get("path") for i in req["items"].values() if i.get("path")), "(not saved yet)")
        if timestamp:
            detail_lines.append(f"When: {timestamp}")
        detail_lines.append(f"URL: {first_url}")
        detail_lines.append(f"Saved to: {first_path}")
        if size_str:
            detail_lines.append(f"Total size: {size_str}")

    dev_lines = []
    if mode == "Developer":
        elapsed = req.get("elapsed_seconds")
        elapsed_str = f"{elapsed:.1f}s" if elapsed else "in progress"
        dev_lines.append(f"{req['request_id']}  [{req.get('overall', 'in progress')}]")
        dev_lines.append(f"type={req['type']}  dtype={req['dtype']}  mode={req['mode']}  items={n_items}")
        dev_lines.append(f"elapsed={elapsed_str}")

    return name_text, detail_lines, dev_lines


def build_request_history_section(app, parent):
    """Builds the mode dropdown + scrollable request list into `parent`.
    Returns a refresh() function - call it any time requests change
    (after a download finishes, after a delete, etc).

    `parent` holds two sibling frames: the list view (everything below)
    and an initially-hidden detail view. Clicking "View" on a request
    hides the list view and builds the request's drill-down INTO the
    detail view with a "<- Back" button - an in-app page swap, not a
    pop-up Toplevel. Back (or Escape) swaps them back and refreshes the
    list so it reflects anything that changed while the detail was open."""
    list_view = ctk.CTkFrame(parent, fg_color="transparent")
    list_view.pack(fill="both", expand=True)
    detail_view = ctk.CTkFrame(parent, fg_color="transparent")
    # detail_view is packed only while a request detail is showing.

    def show_detail(request_id):
        if not (list_view.winfo_exists() and detail_view.winfo_exists()):
            return
        if detail_view.winfo_ismapped():
            return  # already showing a detail page (e.g. a double-click on View)
        if get_request(request_id) is None:
            messagebox.showinfo("Not found", "That request no longer exists (it may have been deleted).")
            refresh()
            return
        list_view.pack_forget()
        detail_view.pack(fill="both", expand=True)
        _render_request_detail(app, detail_view, request_id,
                               list_refresh=refresh, back_callback=show_list)

    def show_list():
        # Any retry thread still holding an old page's render closure will
        # now dispatch to nothing (see render() in _render_request_detail).
        app._request_detail_render = None
        if detail_view.winfo_exists():
            for w in detail_view.winfo_children():
                w.destroy()
            detail_view.pack_forget()
        if list_view.winfo_exists():
            list_view.pack(fill="both", expand=True)
            refresh()

    def _on_escape(_event=None):
        # One app-lifetime binding; a no-op unless the detail page is the
        # thing currently on screen, so it never fights other keybinds.
        if detail_view.winfo_exists() and detail_view.winfo_ismapped():
            show_list()
    app.bind("<Escape>", _on_escape, add="+")

    header = ctk.CTkFrame(list_view, fg_color="transparent")
    header.pack(fill="x", pady=(0, 8))
    ctk.CTkLabel(header, text="Request History", font=app.font_label).pack(side="left")
    ctk.CTkLabel(header, text="Display mode:", font=app.font_small, text_color="gray60").pack(
        side="left", padx=(20, 6))

    mode_var = ctk.StringVar(value="Name")
    app._request_history_mode_var = mode_var

    def on_mode_change(val):
        if val == "Developer" and not getattr(app, "_dev_authenticated", False):
            app._prompt_dev_login_redirect("Developer display mode")
            mode_var.set("Name")
            return
        refresh()

    ScrollableDropdown(header, DISPLAY_MODES, mode_var, font=app.font_small, width=140,
                        command=on_mode_change).pack(side="left")

    def delete_all_clicked():
        in_progress, completed = get_all_requests()
        if not in_progress and not completed:
            return
        if messagebox.askyesno("Delete all request history",
                                f"Delete all {len(in_progress) + len(completed)} request(s)? This only "
                                f"removes the history log, not any downloaded files."):
            for req in in_progress + completed:
                delete_request(req["request_id"])
            refresh()

    def retry_all_clicked():
        _, completed = get_all_requests()
        failed_pairs = []
        for req in completed:
            if req.get("overall") in ("failed", "partial"):
                for url, item in req["items"].items():
                    if item.get("status") == "failed":
                        failed_pairs.append((req["request_id"], url))
        if not failed_pairs:
            messagebox.showinfo("Retry All", "No failed downloads to retry.")
            return
        if not messagebox.askyesno("Retry All", f"Retry {len(failed_pairs)} failed download(s)?"):
            return
        for request_id, url in failed_pairs:
            reopen_for_retry(request_id, url)
        refresh()
        threading.Thread(target=_retry_all_thread, args=(app, failed_pairs, refresh), daemon=True).start()

    ctk.CTkButton(header, text="\u21bb", width=32, font=app.font_normal, fg_color="gray40",
                  hover_color="gray30", command=lambda: refresh()).pack(side="right", padx=(6, 0))
    ctk.CTkButton(header, text="Delete All", width=90, font=app.font_small, fg_color="#a13333",
                  hover_color="#7d2626", command=lambda: delete_all_clicked()).pack(side="right")
    ctk.CTkButton(header, text="Retry All", width=90, font=app.font_small,
                  command=lambda: retry_all_clicked()).pack(side="right", padx=(0, 8))

    # --- Search + filter/sort row ---
    filter_row = ctk.CTkFrame(list_view, fg_color="transparent")
    filter_row.pack(fill="x", pady=(0, 8))
    search_var = ctk.StringVar(value="")
    search_entry = ctk.CTkEntry(filter_row, textvariable=search_var, font=app.font_normal,
                                 placeholder_text="Search request names...")
    search_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
    search_entry.bind("<Return>", lambda e: refresh())
    app._add_search_clear_button(search_entry, search_var)
    search_var.trace_add(
        "write", lambda *_: app._debounced_call("_request_history_search_after_id", 300, refresh))

    ctk.CTkLabel(filter_row, text="Sort:", font=app.font_small, text_color="gray60").pack(side="left", padx=(0, 6))
    sort_var = ctk.StringVar(value="Newest first")
    ScrollableDropdown(filter_row, SORT_MODES, sort_var, font=app.font_small, width=150,
                        command=lambda _v: refresh()).pack(side="left", padx=(0, 8))

    ctk.CTkLabel(filter_row, text="Type:", font=app.font_small, text_color="gray60").pack(side="left", padx=(0, 6))
    type_var = ctk.StringVar(value="All")
    ScrollableDropdown(filter_row, TYPE_FILTERS, type_var, font=app.font_small, width=110,
                        command=lambda _v: refresh()).pack(side="left")

    list_frame = ctk.CTkScrollableFrame(list_view, height=280)
    list_frame.pack(fill="both", expand=True)

    _render_sig = [None]  # mutable cell for the closure

    def refresh():
        in_progress, completed = get_all_requests()
        all_requests = in_progress + completed  # in_progress already sorted newest-first, then completed

        query = search_var.get().strip()
        if query:
            # Weighted multi-field search: a match in the request's name
            # ranks above a match in any of its URLs, which ranks above
            # a match in request_id/type/mode/per-item names - matching
            # the same tiered priority as General History's search.
            scored = []
            for r in all_requests:
                urls = list(r["items"].keys())
                extra = [r["request_id"], r.get("dtype", ""), r.get("mode", "")]
                extra += [item.get("name", "") for item in r["items"].values()]
                score = weighted_match_score(query, _first_title(r), " ".join(urls), extra)
                if score:
                    scored.append((score, r))
            scored.sort(key=lambda pair: pair[0], reverse=True)
            all_requests = [r for _score, r in scored]

        type_choice = type_var.get()
        if type_choice != "All":
            all_requests = [r for r in all_requests if r["dtype"] == type_choice]

        sort_choice = sort_var.get()
        # Sorting (not searching) skips a leading special character in
        # the name - search itself never does, so ".com"/".org" etc
        # still work as literal search text.
        if sort_choice == "Alphabetical (A-Z)":
            all_requests = sorted(all_requests, key=lambda r: strip_leading_special(_first_title(r)).lower())
        elif sort_choice == "Alphabetical (Z-A)":
            all_requests = sorted(all_requests, key=lambda r: strip_leading_special(_first_title(r)).lower(), reverse=True)
        elif sort_choice == "Oldest first":
            all_requests = sorted(all_requests, key=lambda r: r.get("finished_at") or r["created_at"])
        elif sort_choice == "Largest total size":
            all_requests = sorted(all_requests, key=lambda r: _total_size(r) or 0, reverse=True)
        # "Newest first" is already the natural order from get_all_requests()
        # (or, with a search active, the relevance-ranked order from above)

        in_progress_ids = {r["request_id"] for r in in_progress}

        # O2: skip the full teardown/rebuild when nothing visible changed
        # (this fires every ~250ms during a batch).
        sig = (query, type_choice, sort_choice, mode_var.get(), tuple(
            (r["request_id"],
             "in_progress" if r["request_id"] in in_progress_ids else r.get("overall", "failed"),
             _first_title(r), r.get("custom_name"),
             tuple(sorted(i.get("status", "") for i in r["items"].values())))
            for r in all_requests))
        if sig == _render_sig[0] and list_frame.winfo_children():
            return
        _render_sig[0] = sig

        for w in list_frame.winfo_children():
            w.destroy()

        if not all_requests:
            msg = "No download requests yet." if not (in_progress or completed) else "No requests match your search/filter."
            ctk.CTkLabel(list_frame, text=msg, font=app.font_normal, text_color="gray60").pack(pady=20)
            return

        for req in all_requests:
            status_key = "in_progress" if req["request_id"] in in_progress_ids else req.get("overall", "failed")
            _build_request_row(app, list_frame, req, status_key, mode_var.get(), refresh, show_detail)

    refresh()
    return refresh


def _build_request_row(app, parent, req, status_key, mode, refresh_callback, show_detail):
    row = ctk.CTkFrame(parent)
    row.pack(fill="x", pady=3, padx=2)
    row.grid_columnconfigure(1, weight=1)

    _dot(row, DOT_COLORS.get(status_key, "#888888")).grid(row=0, column=0, rowspan=2, padx=(10, 10), pady=10)

    # Name is always shown biggest (font_normal) regardless of mode;
    # Detailed adds its own lines underneath in a smaller, dimmer font;
    # Developer adds a further tier below THAT - each mode shows
    # everything the tier(s) before it would, not just its own info, per
    # how this was specifically asked for. Separate labels (not one
    # flat multi-line string in a single font) is what actually makes
    # the size/color hierarchy visible rather than just implied by line
    # order.
    name_text, detail_lines, dev_lines = _request_display_parts(req, mode)
    text_col = ctk.CTkFrame(row, fg_color="transparent")
    text_col.grid(row=0, column=1, sticky="ew", pady=8)
    ctk.CTkLabel(text_col, text=name_text, font=app.font_normal, anchor="w", justify="left").pack(anchor="w")
    for line in detail_lines:
        ctk.CTkLabel(text_col, text=line, font=app.font_small, text_color="gray60",
                     anchor="w", justify="left").pack(anchor="w")
    for line in dev_lines:
        ctk.CTkLabel(text_col, text=line, font=app.font_small, text_color="gray50",
                     anchor="w", justify="left").pack(anchor="w")

    btns = ctk.CTkFrame(row, fg_color="transparent")
    btns.grid(row=0, column=2, padx=10)
    failed_urls = [u for u, it in req.get("items", {}).items() if it.get("status") == "failed"]
    if failed_urls:
        ctk.CTkButton(btns, text=f"Retry failed ({len(failed_urls)})", width=125, font=app.font_small,
                      command=lambda r=req, fu=list(failed_urls):
                          _retry_request_failed(app, r, fu, refresh_callback)
                      ).pack(side="left", padx=(0, 6))
    ctk.CTkButton(btns, text="View", width=70, font=app.font_small,
                  command=lambda r=req: show_detail(r["request_id"])
                  ).pack(side="left", padx=(0, 6))
    _del = ctk.CTkButton(btns, text="Delete", width=75, font=app.font_small,
                         fg_color="#a13333", hover_color="#7d2626")
    _del.configure(command=lambda b=_del, rid=req["request_id"]: app._arm_delete(
        b, lambda: _delete_request_now(rid, refresh_callback)))
    _del.pack(side="left")


def _retry_request_failed(app, req, failed_urls, refresh_callback):
    """Retry only the failed items of ONE request. The queue counter
    resumes from the number of items in this request that already
    succeeded (so a 20-item request with 6 done shows "Retry 7/20"
    onward), not from 1."""
    if _download_busy(app):
        messagebox.showinfo("Download in progress",
                            "Wait for the current download to finish before retrying.")
        return
    if not failed_urls:
        messagebox.showinfo("Retry failed", "Nothing failed in this request.")
        return
    if not messagebox.askyesno("Retry failed",
                               f"Retry {len(failed_urls)} failed item(s) in this request?"):
        return
    request_id = req["request_id"]
    items = req.get("items", {})
    total = len(items)
    already_done = sum(1 for it in items.values() if it.get("status") == "success")
    for u in failed_urls:
        reopen_for_retry(request_id, u)
    refresh_callback()
    threading.Thread(
        target=_retry_all_thread,
        args=(app, [(request_id, u) for u in failed_urls], refresh_callback),
        kwargs={"counter_base": already_done, "counter_total": total},
        daemon=True).start()


def _delete_request_now(request_id, refresh_callback):
    """Inline two-click delete (#20) - the button already confirmed."""
    delete_request(request_id)
    refresh_callback()


def _delete_request_clicked(request_id, refresh_callback):
    if messagebox.askyesno("Delete request", "Delete this request from history? This only removes the "
                                              "log entry, not any downloaded files."):
        delete_request(request_id)
        refresh_callback()


def _render_request_detail(app, container, request_id, list_refresh, back_callback):
    """The per-request drill-down, rendered as an in-app page INTO
    `container` (the section's hidden detail frame) rather than a pop-up
    Toplevel: every URL in the request, its status dot, Copy Link /
    Retry per URL, an editable title, and the multi-select toolbar - with
    a "<- Back" button at the top that returns to the list view.

    `list_refresh` rebuilds the request list; `back_callback` returns to
    it (also called on Escape, wired once by build_request_history_section).
    """
    for w in container.winfo_children():
        w.destroy()

    req = get_request(request_id)
    if not req:
        messagebox.showinfo("Not found", "That request no longer exists (it may have been deleted).")
        back_callback()
        return

    # --- top bar: Back + editable title ---
    top_row = ctk.CTkFrame(container, fg_color="transparent")
    top_row.pack(fill="x", padx=15, pady=(15, 2))
    ctk.CTkButton(top_row, text="← Back", width=80, font=app.font_small, fg_color="gray40",
                  hover_color="gray30", command=lambda: back_callback()).pack(side="left", padx=(0, 12))

    title_row = ctk.CTkFrame(top_row, fg_color="transparent")
    title_row.pack(side="left", fill="x", expand=True)
    # The name shown here is the request's own display name (its custom
    # name if renamed, otherwise the first downloaded item's title,
    # never the raw internal request_id) - per how this was asked for.
    name_label = ctk.CTkLabel(title_row, text=req.get("custom_name") or _first_title(req),
                              font=app.font_label, anchor="w")
    name_label.pack(side="left", fill="x", expand=True)
    rename_entry = ctk.CTkEntry(title_row, font=app.font_label)
    subtitle_label = ctk.CTkLabel(container, text="", font=app.font_small, text_color="gray60")
    subtitle_label.pack(anchor="w", padx=15)

    def _refresh_header(current):
        """Update the title + subtitle from `current` in place - no
        window teardown/rebuild (this is an embedded page now)."""
        if not name_label.winfo_exists():
            return
        name_label.configure(text=current.get("custom_name") or _first_title(current))
        elapsed = current.get("elapsed_seconds")
        subtitle = f"{request_id} - {current['dtype']} {current['mode']} - {len(current['items'])} item(s)"
        if elapsed:
            subtitle += f" - took {elapsed:.1f}s total"
        size_bytes = _total_size(current)
        if size_bytes is not None:
            subtitle += f" - {format_file_size(size_bytes)} total"
        subtitle_label.configure(text=subtitle)

    def start_rename():
        name_label.pack_forget()
        rename_btn.pack_forget()
        rename_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        rename_entry.delete(0, "end")
        rename_entry.insert(0, name_label.cget("text"))
        rename_entry.focus_set()
        save_btn.pack(side="left")

    def _end_rename():
        rename_entry.pack_forget()
        save_btn.pack_forget()
        name_label.pack(side="left", fill="x", expand=True)
        rename_btn.pack(side="left")

    def save_rename():
        new_name = rename_entry.get().strip()
        rename_request(request_id, new_name)
        _end_rename()
        current = get_request(request_id) or req
        _refresh_header(current)   # update in place, no reopen
        list_refresh()

    rename_btn = ctk.CTkButton(title_row, text="Rename", width=70, font=app.font_small, fg_color="gray40",
                                hover_color="gray30", command=start_rename)
    rename_btn.pack(side="left")
    save_btn = ctk.CTkButton(title_row, text="Save", width=60, font=app.font_small, command=save_rename)
    rename_entry.bind("<Return>", lambda e: save_rename())

    def _cancel_rename(_e=None):
        _end_rename()
        return "break"  # don't let this Escape also bubble to the page-level Back
    rename_entry.bind("<Escape>", _cancel_rename)

    _refresh_header(req)

    # Advanced Selecting for this request's URLs - built ONCE here and
    # reused across every re-render (a retry finishing, a checkbox
    # toggling); render() below only rebuilds the per-URL rows, never
    # this toolbar or the selector instance.
    from gui.advanced_select import AdvancedSelector, build_selection_toolbar
    item_selector = AdvancedSelector()
    select_toolbar = ctk.CTkFrame(container, fg_color="transparent")
    select_toolbar.pack(fill="x", padx=15, pady=(8, 0))

    list_frame = ctk.CTkScrollableFrame(container)
    list_frame.pack(fill="both", expand=True, padx=15, pady=15)

    # url -> (row_frame, signature). A row is only destroyed + rebuilt
    # when its own item actually changed (status/path/name/error) or the
    # selection state affecting it changed - an unchanged row survives a
    # re-render untouched, so a retry finishing on item 3 of 20 no longer
    # rebuilds the other 19.
    row_cache = {}

    def _do_render():
        if not list_frame.winfo_exists():
            return  # user hit Back before this refresh fired - no-op, no crash
        # Fall back to the last-known request dict if it's since been
        # deleted - render stays silent (matches the old Toplevel), the
        # "no longer exists" notice only fires on the initial open.
        current = get_request(request_id) or req
        _refresh_header(current)
        selecting = item_selector.enabled
        items = current["items"]

        for url in list(row_cache):
            if url not in items:
                row_cache[url][0].destroy()
                del row_cache[url]

        ordered = []
        for url, item in items.items():
            sig = (item.get("status"), item.get("path"), item.get("name"), item.get("error"),
                   item.get("error_category"), item.get("error_hint"),
                   selecting, selecting and item_selector.is_selected(url))
            cached = row_cache.get(url)
            if cached is not None and cached[1] == sig and cached[0].winfo_exists():
                ordered.append(cached[0])
                continue
            if cached is not None:
                cached[0].destroy()
            frame = _build_item_row(app, list_frame, request_id, url, item, render,
                                    list_refresh, item_selector)
            row_cache[url] = (frame, sig)
            ordered.append(frame)

        # A rebuilt middle row is re-packed at the end of the frame by
        # _build_item_row; restore the request's real item order so a
        # retry finishing on item 3 doesn't shuffle it to the bottom.
        for i, frame in enumerate(ordered):
            if i == 0:
                frame.pack_configure(side="top", fill="x", pady=3)
            else:
                frame.pack_configure(side="top", fill="x", pady=3, after=ordered[i - 1])

    # The debounce slot lives on `app` and is shared, so a retry thread that
    # captured an OLD page's `render` (then the user hit Back and re-opened
    # the request) must not cancel or drive the live page. Dispatch through
    # this pointer, which show_list() nulls and each _render_request_detail
    # sets to its own _do_render - so a stale render() is a harmless no-op
    # and a live one always hits the current page.
    app._request_detail_render = _do_render

    def render():
        # Coalesce bursts (worker threads fire this per retried item via
        # app.after) into one rebuild, same pattern as _refresh_history_tab.
        app._debounced_call("_request_detail_render_after_id", 120,
                            lambda: (getattr(app, "_request_detail_render", None) or (lambda: None))())

    def copy_selected():
        selected = item_selector.selected_ids()
        if not selected:
            messagebox.showwarning("Copy Link", "No URLs selected.")
            return
        app.clipboard_clear()
        app.clipboard_append("\n".join(selected))
        app._log(f"Copied {len(selected)} URL(s) to clipboard.")

    def retry_selected():
        selected = item_selector.selected_ids()
        if not selected:
            messagebox.showwarning("Retry", "No URLs selected.")
            return
        if _download_busy(app):
            messagebox.showinfo("Download in progress",
                                "Wait for the current download to finish before retrying.")
            return
        for u in selected:
            reopen_for_retry(request_id, u)
        item_selector.clear()
        render()
        threading.Thread(target=_retry_all_thread, args=(app, [(request_id, u) for u in selected], render),
                          daemon=True).start()

    build_selection_toolbar(
        select_toolbar, item_selector,
        all_ids_getter=lambda: list((get_request(request_id) or req)["items"].keys()),
        on_copy=copy_selected, on_download=retry_selected, download_label="Retry Selected",
        font_normal=app.font_normal, font_small=app.font_small)
    base_on_change = item_selector.on_change

    def combined_on_change():
        base_on_change()
        render()
    item_selector.on_change = combined_on_change

    _do_render()


def _build_item_row(app, parent, request_id, url, item, render_callback, parent_refresh_callback, item_selector=None):
    row = ctk.CTkFrame(parent)
    row.pack(fill="x", pady=3)
    col = 0
    if item_selector is not None and item_selector.enabled:
        cb_var = ctk.BooleanVar(value=item_selector.is_selected(url))
        ctk.CTkCheckBox(row, text="", variable=cb_var, width=18,
                        command=lambda u=url: item_selector.toggle(u)).grid(
            row=0, column=0, rowspan=2, padx=(10, 2), pady=10)
        col = 1
    row.grid_columnconfigure(col + 1, weight=1)

    status = item.get("status", "pending")
    color = {"success": "#2fa84f", "failed": "#c0392b",
             "downloading": "#e0c020", "pending": "#888888"}.get(status, "#888888")
    _dot(row, color).grid(row=0, column=col, rowspan=2, padx=(10, 10), pady=10)

    name = item.get("name") or url
    ctk.CTkLabel(row, text=name, font=app.font_normal, anchor="w").grid(row=0, column=col + 1, sticky="w", pady=(8, 0))
    detail = url
    size_str = None
    _size = _file_size_or_none(item.get("path"))
    if _size is not None:
        size_str = format_file_size(_size)
    if item.get("error_category"):
        detail += f"  -  [{item['error_category']}] {item.get('error', '')}"
    elif item.get("error"):
        detail += f"  -  {item['error']}"
    elif item.get("path"):
        detail += f"  -  {item['path']}"
    if size_str:
        detail += f"  -  {size_str}"
    ctk.CTkLabel(row, text=detail, font=app.font_small, text_color="gray60", anchor="w").grid(
        row=1, column=col + 1, sticky="w", pady=(0, 8))
    if item.get("error_hint"):  # F8: one-line "what to do about it"
        ctk.CTkLabel(row, text="→ " + item["error_hint"], font=app.font_small,
                     text_color="#d68910", anchor="w", wraplength=560, justify="left").grid(
            row=2, column=col + 1, sticky="w", pady=(0, 8))

    # Individual Copy Link/Retry stay available even with selecting on -
    # picking a subset for a bulk action doesn't take away the quick
    # single-URL actions, it just adds the checkbox alongside them.
    btns = ctk.CTkFrame(row, fg_color="transparent")
    btns.grid(row=0, column=col + 2, rowspan=2, padx=10)
    ctk.CTkButton(btns, text="Copy Link", width=85, font=app.font_small, fg_color="gray40", hover_color="gray30",
                  command=lambda u=url: _copy_link(app, u)).pack(side="left", padx=(0, 6))
    # Open the downloaded file / its folder straight from the request view
    # (issue #19). Shown only once the item has a real saved path.
    _p = item.get("path")
    if _p:
        ctk.CTkButton(btns, text="Open", width=55, font=app.font_small, fg_color="gray40",
                      hover_color="gray30",
                      command=lambda p=_p: app._open_media_or_warn(p)).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btns, text="Folder", width=60, font=app.font_small, fg_color="gray40",
                      hover_color="gray30",
                      command=lambda p=_p: app._open_or_warn(os.path.dirname(p))).pack(side="left", padx=(0, 6))
    # For a successful item the button becomes "Redownload" (still
    # enabled) - the common reason to want it is that the file was
    # deleted; it fetches again into the request's original out_dir,
    # exactly like a retry.
    is_success = status == "success"
    retry_btn = ctk.CTkButton(btns, text="  Redownload" if is_success else "  Retry",
                               image=_retry_icon(app), compound="left", height=34,
                               width=120 if is_success else 95, font=app.font_normal,
                               command=lambda u=url: _retry_url(app, request_id, u, render_callback,
                                                                 parent_refresh_callback, retry_btn))
    retry_btn.pack(side="left")
    return row


_RETRY_ICON = None


def _retry_icon(app):
    """The circular-arrow retry icon (issue #14 - a picture, bigger button)."""
    global _RETRY_ICON
    if _RETRY_ICON is None:
        try:
            from core.paths import resource_path
            from PIL import Image
            _RETRY_ICON = ctk.CTkImage(Image.open(resource_path("assets/retry_icon.png")),
                                       size=(18, 18))
            # keep a hard ref alive (see gui/app.py's CTkImage note)
            getattr(app, "_kept_images", []).append(_RETRY_ICON) if hasattr(app, "_kept_images") \
                else setattr(app, "_kept_images", [_RETRY_ICON])
        except Exception:
            _RETRY_ICON = None
    return _RETRY_ICON


def _copy_link(app, url):
    app.clipboard_clear()
    app.clipboard_append(url)
    app._log(f"Copied to clipboard: {url}")


def _download_busy(app):
    """True if a normal download or batch is currently running - a retry
    would fight it over the shared app.downloader and the progress bar."""
    if getattr(app, "batch_running", False):
        return True
    btn = getattr(app, "cancel_btn", None)
    try:
        return btn is not None and str(btn.cget("state")) == "normal"
    except Exception:
        return False


def _do_retry_download(app, request_id, url, manage_ui_state=True):
    """The actual retry-download logic for one URL - shared by both the
    single-item Retry button and Retry All, so there's exactly one place
    this logic lives rather than two copies that could drift apart."""
    from core.downloader import (Downloader, DownloadCancelled, DownloadStageError,
                                  YouTubeBotDetectedError, CookieAccessError,
                                  fetch_media_info, download_with_retry)
    from core.utils import make_unique_name, sanitize_filename, beautify_title
    from core.history import add_entry

    req = get_request(request_id)
    if not req:
        app._threadsafe_log(f"Retry aborted - request {request_id} no longer exists.", color="red")
        return
    update_item(request_id, url, status="downloading")

    # Prefer the folder the request was originally pointed at (stored on
    # the request since 1.5.3); fall back to the current output field, then
    # the last folder used this session.
    out_dir = req.get("out_dir") or app._resolve_output_dir() or app.last_output_dir
    if not out_dir:
        update_item(request_id, url, status="failed", error="No output folder available for retry.")
        finish_request(request_id)
        return

    dtype = req["dtype"]
    media_info = None
    try:
        media_info = fetch_media_info(url)
        name = sanitize_filename(beautify_title(media_info.get("title", "download")))
    except Exception:
        name = "download"
    ext = app.cfg["video_format"] if dtype == "Video" else app.cfg["audio_format"]
    unique_name = make_unique_name(out_dir, name, ext)

    # Register this as the app's active download so Cancel and the progress
    # bar / speed readout work during a retry, exactly like a normal one.
    downloader = Downloader(progress_callback=app._threadsafe_progress,
                            log_callback=app._threadsafe_log,
                            ping_ms_provider=lambda: app._network_ping)
    app.downloader = downloader
    if manage_ui_state:
        app.after(0, lambda: app._set_downloading_state(True))
    cookies = app.cfg.get("cookies_from_browser", "none")
    status, path = "Success", ""
    try:
        if dtype == "Video":
            path = download_with_retry(
                downloader.download_video, log_callback=app._threadsafe_log,
                url=url, name=unique_name, out_dir=out_dir,
                quality_key=app.cfg["video_quality"], fmt=app.cfg["video_format"],
                playlist=False, subtitles=app.subtitles_var.get(), aspect_ratio=app.aspect_var.get(),
                cookies_from_browser=cookies, prefetched_info=media_info
            )
        else:
            path = download_with_retry(
                downloader.download_audio, log_callback=app._threadsafe_log,
                url=url, name=unique_name, out_dir=out_dir,
                quality=app.cfg["audio_quality"], fmt=app.cfg["audio_format"],
                playlist=False, embed_thumbnail=app.cfg.get("embed_thumbnail", True),
                cookies_from_browser=cookies
            )
        update_item(request_id, url, status="success", name=unique_name, path=path,
                    elapsed_seconds=downloader.elapsed_seconds())
        app._threadsafe_log(f"Retry succeeded: {path}", color="green")
    except DownloadCancelled:
        status = "Cancelled"
        update_item(request_id, url, status="failed", error="Cancelled")
    except YouTubeBotDetectedError:
        status = "Failed"
        app._handle_bot_detection(request_id, url)
    except CookieAccessError:
        status = "Failed"
        app._handle_cookie_access_error(request_id, url)
    except DownloadStageError as e:
        status = "Failed"
        err = f"Failed during {e.stage}: {e.original}"
        update_item(request_id, url, status="failed", error=err)
        app._threadsafe_log(f"Retry failed: {err}", color="red")
    except Exception as e:
        status = "Failed"
        update_item(request_id, url, status="failed", error=str(e))
        app._threadsafe_log(f"Retry failed: {e}", color="red")
    finally:
        if manage_ui_state:
            app.after(0, lambda: app._set_downloading_state(False))

    add_entry(url, unique_name, dtype, path, status)
    finish_request(request_id)


def _retry_url(app, request_id, url, render_callback, parent_refresh_callback, retry_btn):
    """Retries exactly one URL from a request. Common real-world cases this
    covers: a transient network error that's since cleared up, a video
    that was briefly geo-blocked/region-locked and is now available, a
    livestream that wasn't over yet on the first attempt, or a site that
    rate-limited the first attempt and works fine a moment later."""
    if _download_busy(app):
        messagebox.showinfo("Download in progress",
                            "Wait for the current download to finish before retrying an item.")
        return
    retry_btn.configure(state="disabled", text="Retrying...")
    reopen_for_retry(request_id, url)
    render_callback()
    threading.Thread(target=_retry_url_thread, args=(app, request_id, url, render_callback,
                                                       parent_refresh_callback), daemon=True).start()


def _retry_url_thread(app, request_id, url, render_callback, parent_refresh_callback):
    _do_retry_download(app, request_id, url)
    app.after(0, render_callback)
    app.after(0, parent_refresh_callback)
    app.after(0, app._refresh_history_tab)


def _retry_all_thread(app, pairs, refresh_callback, counter_base=0, counter_total=None):
    """Retries every given (request_id, url) pair one at a time, in the
    background - sequential rather than all-at-once, same reasoning as
    the batch-delay setting elsewhere: not hammering a site (or the
    user's own connection) with a burst of simultaneous requests.

    The queue counter shown in the log/progress row starts at
    counter_base + 1 and runs to counter_total. For a per-request
    "Retry failed", the caller passes counter_base = how many items in
    that request already succeeded and counter_total = the request's
    total item count, so it reads e.g. "Retry 7/20" - it picks up where
    the original run left off rather than restarting from 1."""
    delay_s = max(0, app.cfg.get("batch_delay_seconds", 3))
    total = len(pairs)
    grand_total = counter_total if counter_total is not None else total
    app.after(0, lambda: app._set_downloading_state(True))
    try:
        for i, (request_id, url) in enumerate(pairs, start=1):
            n = counter_base + i
            app.after(0, lambda n=n, gt=grand_total:
                      app.queue_progress_label.configure(text=f"Retry {n}/{gt}"))
            _do_retry_download(app, request_id, url, manage_ui_state=False)
            app.after(0, refresh_callback)
            app.after(0, app._refresh_history_tab)
            # Space out requests the same way the batch queue does - a burst
            # of back-to-back retries is exactly what trips YouTube's bot
            # check. Broken into short sleeps so Cancel stays responsive.
            if delay_s and i < total:
                waited = 0.0
                while waited < delay_s:
                    dl = getattr(app, "downloader", None)
                    if dl is not None and dl._cancel:
                        break
                    time.sleep(min(0.25, delay_s - waited))
                    waited += 0.25
    finally:
        app.after(0, lambda: app._set_downloading_state(False))
        app.after(0, lambda: app.queue_progress_label.configure(text=""))
    app.after(0, lambda: app._threadsafe_log(f"Retry finished ({total} item(s))."))
