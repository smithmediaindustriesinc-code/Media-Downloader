import io
import glob
import json
import os
import shutil
import re
import subprocess
import sys
import threading
import time
import datetime
import urllib.request

import customtkinter as ctk
from tkinter import filedialog, messagebox
from PIL import Image

# Not needed for this app (no runtime monitor-dragging DPI changes to react
# to), and it's one less background .after() polling loop + ctypes call
# running for the app's whole lifetime - trims one moving part out of the
# startup/runtime picture entirely.
ctk.deactivate_automatic_dpi_awareness()

from core.config import load_config, save_config
from core.downloader import (Downloader, DownloadCancelled, DownloadStageError, YouTubeBotDetectedError,
                              CookieAccessError, PlaylistFetchTimeout,
                              fetch_info, fetch_media_info, fetch_playlist_info, cleanup_partial_files, download_with_retry,
                              MAX_DOWNLOAD_ATTEMPTS,
                              VIDEO_QUALITIES, VIDEO_FORMATS, AUDIO_FORMATS, AUDIO_QUALITIES,
                              ASPECT_RATIO_OPTIONS)
from core.error_popup import show_error
from core.download_requests import (start_request, update_item, finish_request, add_item_to_request,
                                     reopen_for_retry, get_all_requests, get_request, delete_request,
                                     find_previous_download)
from core.utils import (open_folder, open_file, open_in_vlc, open_media_smart, make_unique_name, sanitize_filename,
                         beautify_title, weighted_match_score, strip_leading_special,
                         list_files, move_files, format_file_size)
from core.history import load_history, add_entry, update_entry, clear_history, delete_entry
from core.playlists import (list_playlists, create_playlist, delete_playlist, playlist_path,
                             playlist_contents, add_file_to_playlist, remove_file_from_playlist,
                             ensure_playlists_root, import_folder_as_playlist)
from core.paths import resource_path, ensure_media_folders, ensure_playlists_folder, app_dir, install_dir
from core import dependencies as deps
from gui.scrollable_dropdown import ScrollableDropdown
from gui.dialogs import MoveFilesDialog, NewPlaylistDialog
from gui.request_history import build_request_history_section
from gui.sidebar_tabview import SidebarTabview
from core.dev_access import check_credentials as check_dev_credentials, grant_access as grant_dev_access

URL_PATTERN = re.compile(r"https?://\S+")
# DEV_USERNAME / DEV_PASSWORD now live in core/dev_access.py, which also
# handles any additionally-granted developer accounts - see check_dev_credentials.
FONT_FAMILIES = ["Segoe UI", "Arial", "Calibri", "Verdana", "Tahoma", "Consolas",
                  "Courier New", "Georgia", "Trebuchet MS", "Comic Sans MS", "Times New Roman"]
FONT_SIZES = [11, 12, 13, 14, 15, 16, 18, 20, 22, 24]
APPEARANCE_MODES = ["Dark", "Light", "System"]
COLOR_THEMES = ["blue", "dark-blue", "green", "gold", "purple", "red", "teal", "rose", "slate", "orange", "cyan", "indigo"]
TAB_ICON_SIZE = (18, 18)
TAB_ICONS = {
    "Download": "assets/tabs/download_icon.png",
    "Media": "assets/tabs/media_icon.png",
    "History": "assets/tabs/history_icon.png",
    "Settings": "assets/tabs/settings_gear.png",
    "More": "assets/tabs/more_icon.png",
    "Version": "assets/tabs/version_icon.png",
    "Developer": "assets/tabs/developer_icon.png",
}
# yt-dlp's own supported browser names for --cookies-from-browser - "none"
# means don't use cookies at all (the default, no behavior change).
COOKIE_BROWSER_OPTIONS = ["none", "chrome", "firefox", "edge", "brave", "opera", "vivaldi", "safari"]
COOKIE_BROWSER_LABELS = {v: ("None" if v == "none" else v.title()) for v in COOKIE_BROWSER_OPTIONS}
HISTORY_SORT_MODES = ["Newest first", "Oldest first", "Alphabetical (A-Z)", "Alphabetical (Z-A)", "Largest file size"]
HISTORY_TYPE_FILTERS = ["All", "Video", "Audio"]
COLOR_THEME_LABELS = {v: v.replace("-", " ").title() for v in COLOR_THEMES}
BUILTIN_COLOR_THEMES = {"blue", "green", "dark-blue", "gold"}
VLC_BUTTON_COLORS = {"fg_color": "#ff8800", "hover_color": "#cc6d00"}


def apply_color_theme(theme_value):
    """blue/green/dark-blue/gold are customtkinter's own built-in themes -
    passed straight through. Everything else is one of our own theme JSON
    files under assets/themes/<name>.json."""
    if theme_value in BUILTIN_COLOR_THEMES:
        ctk.set_default_color_theme(theme_value)
    else:
        ctk.set_default_color_theme(resource_path(f"assets/themes/{theme_value}.json"))


def read_disclaimer():
    try:
        with open(resource_path("DISCLAIMER.txt"), "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "Disclaimer file not found."


class App(ctk.CTk):
    def __init__(self):
        from core.startup_log import mark
        mark("App.__init__ start (before super().__init__())")
        super().__init__()
        mark("ctk.CTk.__init__ done")

        # Flipped once, in _on_close_requested(), before self.destroy()
        # is called. Every recurring background loop (_start_recurring's
        # tick(), the network monitor's background-thread callback)
        # checks this before touching the window again - without it,
        # those loops kept firing (and rescheduling themselves) forever
        # after the window was gone, each hitting "Too early to create
        # image: no default root window" the moment they tried to build
        # a CTkImage against a Tk root that no longer existed. See
        # crash_log.txt / _apply_network_status for what that looked
        # like in practice.
        self._closing = False

        from core.crash_log import install_tk_report_callback
        install_tk_report_callback(self)
        mark("tk report_callback_exception installed")

        self.cfg = load_config()
        mark("config loaded")
        # Picks up a settings file staged by the installer on a fresh
        # install (see installer.iss's CurStepChanged + core/config.py's
        # check_and_apply_pending_import) - a no-op on every ordinary
        # launch where nothing was staged.
        try:
            from core.config import check_and_apply_pending_import
            applied, _import_report = check_and_apply_pending_import()
            if applied:
                self.cfg = load_config()  # re-read - the import already wrote the merged result
                mark("pending installer-staged settings import applied")
        except Exception as e:
            mark(f"pending import check failed (non-fatal): {e}")
        ctk.set_appearance_mode(self.cfg["appearance_mode"])
        apply_color_theme(self.cfg["color_theme"])
        mark("appearance mode + color theme set")

        self.title("Media Downloader")
        self._apply_launch_geometry()
        self.minsize(700, 600)
        self.resizable(True, True)
        mark("window title/geometry set")

        self._resize_overlay = None
        self._resize_settle_after_id = None
        self._last_window_size = (self.winfo_width(), self.winfo_height())
        # Debounced: <Configure> fires continuously (many times a second)
        # for the entire duration of a live resize drag, not just once
        # at the end - binding straight to it without debouncing would
        # mean constantly creating/destroying the overlay throughout
        # the drag, which is worse than no overlay at all. Instead,
        # every Configure event just (re)starts a short timer; the
        # overlay only actually comes down once that timer completes
        # with no FURTHER Configure event resetting it - i.e. once the
        # window size has genuinely "dropped"/settled, not mid-drag.
        self.bind("<Configure>", self._on_window_configure)

        if self.cfg.get("background_color"):
            try:
                self.configure(fg_color=self.cfg["background_color"])
            except Exception:
                pass  # an invalid/stale saved color should never block startup
        mark("background color applied")

        try:
            self.iconbitmap(resource_path("icon.ico"))
            mark("iconbitmap set successfully")
        except Exception as e:
            mark(f"iconbitmap failed (non-fatal, continuing): {e}")

        self.downloader = None
        self.batch_running = False
        self.last_output_dir = None
        self.last_downloaded_path = None
        self._batch_item_durations = []  # completed-item durations, for the whole-queue ETA estimate
        self._batch_items_remaining = 0
        self.thumbnail_image = None
        # Small thumbnails (~160x90) are cheap enough to just never
        # garbage-collect for the life of the app. This works around a
        # real customtkinter/Tk quirk: once a CTkImage's Python object is
        # garbage-collected, a *later* configure() call on the same label
        # with a brand-new CTkImage can still raise
        # _tkinter.TclError: image "pyimageN" doesn't exist - referencing
        # the old, now-cleaned-up image's internal Tcl name, not the new
        # one. Keeping every CTkImage we've ever shown alive in this list
        # sidesteps that entirely. Capped so a very long session doesn't
        # grow unbounded (each entry is trivial in size regardless).
        self._thumbnail_image_history = []
        self._last_clipboard = ""
        # Initialized here (not just inside _apply_network_status, which
        # only runs once the periodic network check thread has completed
        # at least once) so a download started in the first few seconds
        # after launch - before that first check finishes - doesn't hit
        # an AttributeError from Downloader's ping_ms_provider callback.
        self._network_tier = "none"
        self._network_ping = None
        self._speed_tick_active = False
        self._last_progress_pct = 0

        mark("about to call _build_fonts()")
        self._build_fonts()
        mark("_build_fonts() done, about to call _build_ui()")
        self._build_ui()
        mark("_build_ui() done")
        self.grid_columnconfigure(0, weight=1)

        self._start_clipboard_watch()
        mark("clipboard watch started")

        # The window is never withdrawn/hidden during startup (there's
        # nothing that needs to block it - the download folder is now
        # prompted for lazily, at download time, not here), so there's no
        # deiconify()/lift()/forced-topmost dance needed either. It's just
        # a normal window, visible as soon as it's built, exactly like any
        # other Tkinter app. That show/hide/refocus sequence used to be
        # here to compensate for an earlier self.withdraw() call that no
        # longer serves any purpose - removed entirely, see Update
        # Helper.txt section 15 for why.
        mark(f"window state is '{self.state()}' - should be visible without any extra show/focus tricks")

        self.protocol("WM_DELETE_WINDOW", self._on_close_requested)
        self._start_heartbeat()
        self._start_autosave()
        self._restore_draft_fields()
        self._recover_interrupted_downloads()

    RESOLUTION_PRESETS_BASE = [
        (3840, 2160), (2560, 1440), (1920, 1080), (1600, 900),
        (1366, 768), (1280, 720), (1024, 768), (800, 600), (700, 600),
    ]

    def _resolution_preset_choices(self):
        """'Fullscreen' plus every preset that actually fits the primary
        monitor, largest first, down to the app's own enforced minimum
        (700x600) - so the Settings dropdown never offers a size the
        window couldn't actually be."""
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        choices = ["Remembered (last closed size)", "Fullscreen"]
        for w, h in self.RESOLUTION_PRESETS_BASE:
            if w <= screen_w and h <= screen_h:
                choices.append(f"{w}x{h}")
        return choices

    def _apply_launch_geometry(self):
        """Decides window size+position at startup: either the exact
        remembered size/position from the last normal close (clamped to
        stay fully on-screen), a chosen preset resolution (applied once,
        at launch only), Fullscreen (a genuine OS-level maximize, not
        just a window sized to match the screen dimensions), or - on a
        genuinely first-ever launch with nothing saved yet - centered at
        the config default size."""
        self.update_idletasks()
        choice = self.cfg.get("launch_resolution", "Remembered")

        if choice == "Fullscreen":
            # state("zoomed") is a REAL OS-level maximize on Windows -
            # correctly accounts for the taskbar, registers as
            # "Maximized" for snap/restore/double-click-titlebar
            # behavior, all the things "I want it in the maximized
            # view" actually implies. Manually setting geometry to the
            # screen's raw pixel dimensions (what this used to also do,
            # redundantly, alongside this same state() call) is NOT the
            # same thing - it can produce a plain full-size floating
            # window instead of a true maximized one, which is exactly
            # the bug this was about.
            try:
                self.state("zoomed")
                return
            except Exception:
                pass
            try:
                self.attributes("-zoomed", True)  # some Linux window managers' equivalent
                return
            except Exception:
                pass
            # Last-resort fallback if neither maximize approach is
            # supported on this platform at all - at least fill the
            # screen rather than doing nothing.
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            self.geometry(f"{screen_w}x{screen_h}+0+0")
            return

        if choice not in ("Remembered", "Remembered (last closed size)") and "x" in choice:
            try:
                w, h = (int(p) for p in choice.split("x"))
                self._center_window(w, h)
                return
            except ValueError:
                pass  # fall through to remembered/default below

        width = self.cfg.get("window_width", 820)
        height = self.cfg.get("window_height", 720)
        x = self.cfg.get("window_x")
        y = self.cfg.get("window_y")
        if x is not None and y is not None:
            x, y = self._clamp_to_screen(x, y, width, height)
            self.geometry(f"{width}x{height}+{x}+{y}")
            return
        self._center_window(width, height)

    def _on_window_configure(self, event):
        """Called on every <Configure> event - Tk fires this for window
        moves too, not just resizes, so this only actually reacts when
        the SIZE genuinely changed (comparing against the last known
        size), and only for the root window itself (event.widget is
        not self covers Configure events bubbling up from child
        widgets resizing/moving for unrelated reasons, e.g. a popup
        opening - not what this is about)."""
        if event.widget is not self:
            return
        new_size = (self.winfo_width(), self.winfo_height())
        if new_size == self._last_window_size:
            return
        self._last_window_size = new_size
        self._show_resize_overlay()
        if self._resize_settle_after_id is not None:
            self.after_cancel(self._resize_settle_after_id)
        self._resize_settle_after_id = self.after(200, self._hide_resize_overlay)

    def _show_resize_overlay(self):
        """A simple blank frame the exact size of the whole window,
        raised on top of everything else - "unrender" in the sense that
        it visually hides whatever's underneath (which keeps genuinely
        resizing/reflowing live during a drag) rather than the user
        seeing that reflow happen frame by frame. Nothing underneath is
        actually torn down or rebuilt - once this comes back off, it's
        the exact same, already-there GUI revealed again, not a fresh
        render."""
        if self._resize_overlay is not None and self._resize_overlay.winfo_exists():
            return  # already showing - a mid-drag Configure event shouldn't recreate it
        overlay = ctk.CTkFrame(self, fg_color=self.cget("fg_color"))
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        overlay.lift()
        self._resize_overlay = overlay

    def _hide_resize_overlay(self):
        """Called once the debounce timer completes with no further
        resize activity in between - the window size has genuinely
        "dropped"/settled, per how this was specifically asked for."""
        if self._resize_overlay is not None and self._resize_overlay.winfo_exists():
            self._resize_overlay.destroy()
        self._resize_overlay = None
        self._resize_settle_after_id = None

    def _debounced_call(self, after_id_attr, delay_ms, func):
        """Generic debounce helper: cancels whatever's currently
        scheduled under after_id_attr (a distinct attribute name per
        call site, since each needs its own independent timer) and
        reschedules func for delay_ms from now. Collapses rapid-fire
        triggers - every keystroke while typing a search, every tick
        while dragging a slider - into a single actual call once things
        pause, rather than one full rebuild per event.

        This is the fix for a real, confirmed problem: every search box
        in the app was rebuilding its ENTIRE list from scratch (destroy
        every row, rebuild every row) on every single keystroke, via a
        StringVar trace with no debouncing at all - a genuine,
        worsening-with-list-size performance stutter, and very likely
        the actual cause of "reloading is inconsistent on the Media
        tab" too: fast typing could fire a new rebuild before a
        previous one had even finished destroying its old widgets,
        which is exactly the kind of overlapping-call scenario that
        produces inconsistent/glitchy partial-render results."""
        existing = getattr(self, after_id_attr, None)
        if existing is not None:
            try:
                self.after_cancel(existing)
            except Exception:
                pass
        new_id = self.after(delay_ms, func)
        setattr(self, after_id_attr, new_id)

    def _slider_width(self):
        """25% of the current window width, per how this was specifically
        asked for - with a sensible floor so a slider never becomes
        unusably tiny on a small/minimized window. Computed fresh each
        time a slider is built rather than cached, so it reflects
        whatever size the window actually is at that moment."""
        try:
            return max(150, int(self.winfo_width() * 0.25))
        except Exception:
            return 220  # a reasonable default if winfo_width() isn't ready yet

    def _estimate_wrapped_text_height(self, text, font, width_px, side_padding=24, top_bottom_padding=16):
        """How tall a text box needs to be to show `text` fully at
        `width_px` wide, without scrolling - a real word-wrap simulation
        using the font's own actual character measurements
        (font.measure()), not a rough guess, so it's accurate regardless
        of font family/size. Used for the disclaimer box, which is sized
        to its own content rather than a fixed guessed height."""
        usable_width = max(20, width_px - side_padding)
        line_height = font.metrics("linespace")
        total_lines = 0
        for paragraph in text.split("\n"):
            if not paragraph.strip():
                total_lines += 1
                continue
            words = paragraph.split(" ")
            current_line = ""
            for word in words:
                candidate = f"{current_line} {word}".strip()
                if font.measure(candidate) <= usable_width or not current_line:
                    current_line = candidate
                else:
                    total_lines += 1
                    current_line = word
            total_lines += 1  # the final in-progress line of this paragraph
        return total_lines * line_height + top_bottom_padding

    def _clamp_to_screen(self, x, y, width, height):
        """Keeps a window's entire rectangle within the primary screen's
        visible bounds - not just checking the top-left corner is
        roughly reasonable (the old, looser check), but genuinely
        constraining x/y so the whole window stays fully on-screen. This
        is what makes "Remembered" behave the same way Fullscreen always
        has re: never opening somewhere unreachable - if a monitor
        arrangement changed since last close and the remembered position
        would now put part of the window off-screen, it's pulled back
        onto the screen instead."""
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        max_x = max(0, screen_w - width)
        max_y = max(0, screen_h - height)
        return max(0, min(x, max_x)), max(0, min(y, max_y))

    def _center_window(self, width, height):
        """Explicitly compute a centered position on the primary screen,
        rather than leaving placement to the window manager's default -
        which, combined with setting geometry while withdrawn, can land the
        window off-screen or behind other windows on some Windows setups."""
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    # ------------------------------------------------------------------ #
    # Download folder setup - prompted lazily, at download time, not startup
    # ------------------------------------------------------------------ #
    def _prompt_for_download_root(self):
        """Ask the user to pick a default download folder. Called only when
        a download actually needs one and none is set yet - not at startup."""
        messagebox.showinfo(
            "Choose a download folder",
            "Pick a folder where your downloads will be organized into "
            "Videos/ and Music/ subfolders. You can change this anytime in Settings."
        )
        chosen = filedialog.askdirectory(title="Select default download folder")
        if not chosen:
            return False
        video_path, music_path = ensure_media_folders(chosen)
        playlists_path = ensure_playlists_folder(chosen)
        self.cfg["download_root"] = chosen
        self.cfg["video_path"] = video_path
        self.cfg["music_path"] = music_path
        self.cfg["playlists_path"] = playlists_path
        save_config(self.cfg)
        if hasattr(self, "download_root_label"):
            self.download_root_label.configure(text=chosen)
        return True

    def _ensure_download_root_for_download(self):
        """Called right before a download starts. Returns True if a valid
        download root is available (prompting the user if needed), False
        if the user cancelled - in which case the download should abort."""
        root = self.cfg.get("download_root", "")
        if root and os.path.isdir(root):
            return True
        return self._prompt_for_download_root()

    # ------------------------------------------------------------------ #
    def _build_fonts(self):
        fam = self.cfg["font_family"]
        size = self.cfg["font_size"]
        weight = "bold" if self.cfg.get("bold_text") else "normal"
        self.font_title = ctk.CTkFont(family=fam, size=size + 8, weight="bold")
        self.font_label = ctk.CTkFont(family=fam, size=size, weight="bold")
        self.font_normal = ctk.CTkFont(family=fam, size=size, weight=weight)
        self.font_small = ctk.CTkFont(family=fam, size=max(size - 2, 9), weight=weight)
        # Main category headers (Files, Download Defaults, Appearance,
        # etc) - larger AND bold, bumped by 2 more sizes on top of the
        # original +3 (now +5 total) so they read even more clearly as
        # a distinct section break, not just bold text in a long list.
        self.font_section = ctk.CTkFont(family=fam, size=size + 5, weight="bold")
        # Sub-category headers (Video Defaults, Theme, Log Display, etc)
        # - bigger than ordinary text but clearly a level below
        # font_section, and no longer prefixed with "-" (see _sub_header).
        self.font_subsection = ctk.CTkFont(family=fam, size=size + 2, weight="bold")

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(15, 5))
        header.grid_columnconfigure(0, weight=1)
        title_label = ctk.CTkLabel(header, text="Media Downloader", font=self.font_title)
        title_label.grid(row=0, column=0, sticky="w")

        # Mini progress bar, shown only on tabs OTHER than Download (where
        # the full-size one is already visible) - same width as the
        # title text above it, so it reads as a compact status strip
        # rather than a random extra bar. Mirrors self.progress_bar's
        # value exactly (see _set_progress).
        #
        # ALWAYS gridded now (row space permanently reserved) - never
        # grid_remove()'d based on the active tab. That used to remove
        # this row from the layout entirely on the Download tab, which
        # meant everything below it (the whole sidebar + content area)
        # physically shifted position on every single tab switch - a
        # real, confirmed performance/visual-artifact bug. Simply always
        # mirrors self.progress_bar's value now (harmless redundancy on
        # the Download tab, which already has the real progress bar
        # right there) rather than trying to visually hide it - CTkProgressBar
        # doesn't actually support fg_color="transparent" (raises
        # ValueError), so this is also the simpler, more robust fix.
        self.mini_progress_bar = ctk.CTkProgressBar(header, width=180, height=6)
        self.mini_progress_bar.set(0)
        self.mini_progress_bar.grid(row=1, column=0, sticky="w", pady=(2, 0))

        # Barebones one-line echo of the log's last message, visible only
        # on non-Download tabs too - so there's SOME feedback that
        # something is happening without needing to switch back to
        # Download to see the real log. Deliberately just one line, no
        # styling beyond the color the real log line already has. Same
        # always-gridded reasoning as mini_progress_bar above.
        self.mini_log_label = ctk.CTkLabel(header, text="", font=self.font_small, text_color="gray60", anchor="w")
        self.mini_log_label.grid(row=2, column=0, sticky="w", pady=(2, 0))
        self._last_mini_log_text = ""

        # Save status - always visible (not gated to non-Download tabs
        # like the log echo above it), right below it under the title.
        # Turns to "Not saved" the INSTANT a tracked field actually
        # changes (not waiting for a timer), but only turns back to
        # "Up to date" after 3 consecutive auto-save cycles have passed
        # with nothing new changing - see _update_save_status(), called
        # from _autosave_tick.
        self.save_status_label = ctk.CTkLabel(header, text="Up to date", font=self.font_small,
                                               text_color="gray60", anchor="w")
        self.save_status_label.grid(row=3, column=0, sticky="w", pady=(2, 0))
        self._save_status_clean_ticks = 0
        self._save_status_last_snapshot = None

        # Internet status indicator, top-right: one icon that swaps between
        # 4 pre-rendered images (rather than 4 separate widgets) so it
        # reads as a single indicator that changes, plus a color-matched
        # ping/quality label next to it.
        net_frame = ctk.CTkFrame(header, fg_color="transparent")
        net_frame.grid(row=0, column=1, sticky="e")
        self.network_icon_label = ctk.CTkLabel(net_frame, text="", image=None)
        self.network_icon_label.pack(side="left", padx=(0, 6))
        self.network_status_label = ctk.CTkLabel(net_frame, text="Checking...", font=self.font_small,
                                                  text_color="gray60")
        self.network_status_label.pack(side="left")
        self._network_icon_cache = {}
        self._start_network_monitor()

        self.dep_banner = ctk.CTkLabel(self, text="", font=self.font_small, text_color="#e0a020")
        self.dep_banner.grid(row=1, column=0, sticky="w", padx=20)

        self.tabview = SidebarTabview(self, command=self._on_main_tab_changed,
                                       loading_delay_provider=self._get_loading_delay_setting)
        self.tabview.grid(row=2, column=0, sticky="nsew", padx=20, pady=(10, 15))
        self.grid_rowconfigure(2, weight=1)

        # Order requested: Download, Playlists, History, Settings, More,
        # Developer, then Version at the bottom - Developer only ever
        # gets added later, dynamically, after a real dev login (see
        # _dev_login_clicked), so it naturally lands in the right spot
        # without needing special-casing here (added right before Version
        # below, since Version needs to stay last).
        tab_download = self.tabview.add("Download")
        tab_media = self.tabview.add("Media")
        tab_history = self.tabview.add("History")
        tab_settings = self.tabview.add("Settings")
        tab_more = self.tabview.add("More")
        tab_version = self.tabview.add("Version")

        self._build_download_tab(tab_download)
        self._build_media_tab(tab_media)
        self._build_history_tab(tab_history)
        self._build_settings_tab(tab_settings)
        self._build_more_tab(tab_more)
        self._build_version_tab(tab_version)

        # The Developer tab does NOT exist until a correct dev login
        # happens in the More tab - see _dev_login_clicked(). This is
        # different from the old permanent password-locked "Dev" tab:
        # now the tab itself is only created on demand.
        self._dev_authenticated = False
        self._dev_tab_built = False

        # Every sidebar tab icon (settings gear, more/three-dot, and the
        # ones for Download/Media/History/Version/Developer) is applied
        # through the same shared helper, so they're genuinely consistent
        # with each other - not just each individually "looking okay".
        # TAB_ICONS/TAB_ICON_SIZE are module-level so _open_developer_tab
        # can reuse the exact same mapping+size later, once that tab
        # actually exists (it's added dynamically, well after this runs).
        self._apply_tab_icon("Download")
        self._apply_tab_icon("Media")
        self._apply_tab_icon("History")
        self._apply_tab_icon("Settings")
        self._apply_tab_icon("More")
        self._apply_tab_icon("Version")

        self._refresh_dependency_banner()
        self.tabview.set("Download")  # land on Download regardless of .add() order side-effects above

    # ================================================================== #
    # DOWNLOAD TAB
    # ================================================================== #
    def _build_download_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        # Everything lives inside a scrollable outer frame now, not
        # gridded straight onto the tab - a large log-box height (user-
        # adjustable in Settings) plus the rest of this tab's content can
        # genuinely exceed the window's minimum size with nothing
        # scrollable to reach the bottom of it otherwise. Confirmed this
        # was really happening (log box bottom edge landing ~300px past
        # the window's own bottom edge at the enforced minimum size)
        # before adding this wrapper.
        outer = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        outer.grid(row=0, column=0, sticky="nsew")
        outer.grid_columnconfigure(0, weight=1)

        shared = ctk.CTkFrame(outer)
        shared.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        shared.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(shared, text="Type", font=self.font_label).grid(row=0, column=0, sticky="w", padx=15, pady=(12, 4))
        self.type_var = ctk.StringVar(value="Video")
        ScrollableDropdown(shared, ["Video", "Audio"], self.type_var, font=self.font_normal,
                            width=150, command=self._on_type_change).grid(row=0, column=1, sticky="w", padx=15, pady=(12, 4))

        self.playlist_var = ctk.BooleanVar(value=self.cfg.get("default_playlist", False))
        ctk.CTkSwitch(shared, text="Full playlist", font=self.font_normal,
                      variable=self.playlist_var).grid(row=0, column=2, sticky="e", padx=10, pady=(12, 4))

        self.subtitles_var = ctk.BooleanVar(value=self.cfg.get("default_subtitles", False))
        self.subtitles_check = ctk.CTkSwitch(shared, text="Subtitles", font=self.font_normal,
                                              variable=self.subtitles_var)
        self.subtitles_check.grid(row=0, column=3, sticky="e", padx=15, pady=(12, 4))

        ctk.CTkLabel(shared, text="Aspect ratio", font=self.font_label).grid(
            row=1, column=0, sticky="w", padx=15, pady=(4, 4))
        self.aspect_var = ctk.StringVar(value=self.cfg.get("aspect_ratio", "Any"))
        self.aspect_dropdown = ScrollableDropdown(shared, ASPECT_RATIO_OPTIONS, self.aspect_var,
                                                    font=self.font_normal, width=220)
        self.aspect_dropdown.grid(row=1, column=1, sticky="w", padx=15, pady=(4, 4))

        ctk.CTkLabel(shared, text="Output folder (blank = default)", font=self.font_label).grid(
            row=2, column=0, columnspan=4, sticky="w", padx=15, pady=(10, 2))
        out_row = ctk.CTkFrame(shared, fg_color="transparent")
        out_row.grid(row=3, column=0, columnspan=4, sticky="ew", padx=15, pady=(0, 6))
        out_row.grid_columnconfigure(0, weight=1)
        self.output_entry = ctk.CTkEntry(out_row, placeholder_text="Default: video/music base path", font=self.font_normal)
        self.output_entry.grid(row=0, column=0, sticky="ew")
        self._add_clear_button(out_row, self.output_entry).grid(row=0, column=1, padx=(6, 6))
        ctk.CTkButton(out_row, text="Select folder...", width=110, font=self.font_normal,
                      command=self.browse_output).grid(row=0, column=2, padx=(0, 6))
        ctk.CTkButton(out_row, text="Open folder", width=95, font=self.font_normal,
                      fg_color="gray40", hover_color="gray30",
                      command=self.open_current_output_folder).grid(row=0, column=3, padx=(0, 6))
        ctk.CTkButton(out_row, text="Open in VLC", width=95, font=self.font_normal,
                      **VLC_BUTTON_COLORS,
                      command=self.open_output_in_vlc).grid(row=0, column=4)

        playlist_out_row = ctk.CTkFrame(shared, fg_color="transparent")
        playlist_out_row.grid(row=4, column=0, columnspan=4, sticky="ew", padx=15, pady=(0, 12))
        # Label on its own line, dropdown filling the full row width below
        # it - packed side-by-side with a fixed-width dropdown was getting
        # clipped at the end on anything but a wide window, since the
        # label text plus a 220px button could easily exceed the
        # available row width with no wrapping.
        ctk.CTkLabel(playlist_out_row, text="or", font=self.font_label, text_color="gray60",
                     anchor="center").pack(fill="x", pady=(0, 4))
        self.output_playlist_var = ctk.StringVar(value="Select a Playlist...")
        self.output_playlist_dropdown = ScrollableDropdown(
            playlist_out_row, ["Select a Playlist..."], self.output_playlist_var,
            font=self.font_normal, width=320, command=self._on_output_playlist_selected)
        self.output_playlist_dropdown.pack(fill="x")

        self.inner_tabview = ctk.CTkTabview(outer, height=260)
        self.inner_tabview.grid(row=1, column=0, sticky="nsew", pady=(0, 10))
        single_tab = self.inner_tabview.add("Single Download")
        batch_tab = self.inner_tabview.add("Batch Queue")
        self._build_single_tab(single_tab)
        self._build_batch_tab(batch_tab)

        # Progress
        self.progress_bar = ctk.CTkProgressBar(outer)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        # Fixed height, with grid_propagate turned off - this row's three
        # labels (progress %, queue position, ETA) go from empty strings
        # to real text and back constantly during a download, and an
        # ordinary auto-sized frame reflows its own height every time
        # that happens. That's exactly what caused the visual artifacts
        # when switching to/from the Download tab: everything below this
        # row (the batch/single sub-tabview, log box, etc) would jump up
        # or down by a few pixels depending on whether this row happened
        # to be empty or populated at that exact moment. Locking the
        # height here means the row always takes the same space
        # regardless of its content, so nothing below it ever shifts.
        progress_info_row = ctk.CTkFrame(outer, fg_color="transparent", height=26)
        progress_info_row.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        progress_info_row.grid_propagate(False)
        self.progress_label = ctk.CTkLabel(progress_info_row, text="Idle", font=self.font_small, text_color="gray60")
        self.progress_label.pack(side="left")
        # Shown side by side with the per-item progress label above -
        # previously both pieces of information fought over the exact
        # same label, so the "Queue item X/Y" text got stomped almost
        # immediately by the next per-item percentage update. Now they're
        # two independent labels that can both stay visible at once.
        self.queue_progress_label = ctk.CTkLabel(progress_info_row, text="", font=self.font_small,
                                                  text_color="gray60")
        self.queue_progress_label.pack(side="left", padx=(20, 0))
        # A third, independent label for time estimates - "this item" ETA
        # comes straight from yt-dlp's own progress hook (remaining bytes
        # / current speed); "whole queue" is estimated from the average
        # duration of items completed so far in this run, times how many
        # are left, plus whatever's left of the current item.
        self.eta_label = ctk.CTkLabel(progress_info_row, text="", font=self.font_small, text_color="gray60")
        self.eta_label.pack(side="left", padx=(20, 0))

        action_row = ctk.CTkFrame(outer, fg_color="transparent")
        action_row.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.cancel_btn = ctk.CTkButton(action_row, text="Cancel current download", font=self.font_normal,
                                         height=32, fg_color="#a13333", hover_color="#7d2626", state="disabled",
                                         command=self.cancel_download)
        self.cancel_btn.pack(side="left")
        ctk.CTkButton(action_row, text="Clear log", font=self.font_normal, height=32,
                      fg_color="gray40", hover_color="gray30",
                      command=self.clear_log).pack(side="left", padx=(10, 0))
        ctk.CTkLabel(action_row, text="Log mode:", font=self.font_small, text_color="gray60").pack(
            side="left", padx=(20, 6))
        # Always list all 3 - Developer access is gated at SELECTION time
        # in _on_log_mode_changed instead of by rebuilding this dropdown,
        # so toggling the dev feature switch in the Developer tab takes
        # effect immediately without needing to rebuild the Download tab
        # (the same pattern used for Request History's mode dropdown).
        log_modes = ["Simple", "Detailed", "Developer"]
        self._log_mode_var = ctk.StringVar(value="Simple")
        ScrollableDropdown(action_row, log_modes, self._log_mode_var, font=self.font_small,
                            width=130, command=self._on_log_mode_changed).pack(side="left")

        self.log_enabled_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(action_row, text="Log enabled", font=self.font_small, variable=self.log_enabled_var,
                      command=self._on_log_enabled_changed).pack(side="left", padx=(20, 0))

        # Log - a fixed height here (not weight=1/minsize, which only
        # mean something in a plain grid, not inside a scrollable frame's
        # content-sized canvas) driven directly by the Settings slider.
        # Being inside `outer` means if this + everything above it is
        # taller than the visible tab area, the tab simply scrolls to
        # reach it - which is the actual fix for the clipping bug, not
        # just a cosmetic change.
        self._log_entries = []  # backing list of (level, message, color) - see _log()
        self._download_tab_frame = outer  # kept for backwards compat / any future direct references
        self.log_box = ctk.CTkTextbox(outer, font=self.font_small, wrap="word",
                                       height=self.cfg.get("log_box_height", 140))
        self.log_box.grid(row=5, column=0, sticky="nsew", pady=(10, 0))
        # A deliberately tiny, static placeholder for when the log is
        # turned off - not a live-updating feed (that would defeat the
        # point of disabling it), just enough to reassure the user the
        # app hasn't frozen. Occupies the same grid cell as the real log,
        # only one of the two is ever gridded in at a time.
        self.log_disabled_placeholder = ctk.CTkLabel(
            outer, text="Log is turned off - the app is still working normally.",
            font=self.font_small, text_color="gray50",
            height=self.cfg.get("log_box_height", 140), anchor="n")
        self.log_disabled_placeholder.grid(row=5, column=0, sticky="nsew", pady=(10, 0))
        self.log_disabled_placeholder.grid_remove()
        self._disclaimer_text = read_disclaimer()  # shown on the Extras tab now, not the log
        self.log_box.configure(state="disabled")
        self._bind_save_status_tracking()

    def _bind_save_status_tracking(self):
        """Every field the draft-state autosave tracks (see
        _save_draft_fields) also marks the save-status indicator dirty
        the instant it's actually edited - <KeyRelease> is enough since
        these are all plain text fields, no need for anything fancier."""
        for widget in (self.url_entry, self.name_entry, self.output_entry,
                       self.queue_name_entry, self.batch_box):
            widget.bind("<KeyRelease>", self._mark_save_dirty)

    def _build_single_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tab, text="URL", font=self.font_label).grid(row=0, column=0, sticky="w", padx=5, pady=(10, 0))
        url_row = ctk.CTkFrame(tab, fg_color="transparent")
        url_row.grid(row=1, column=0, sticky="ew", padx=5, pady=(4, 0))
        url_row.grid_columnconfigure(0, weight=1)
        self.url_entry = ctk.CTkEntry(url_row, placeholder_text="https://...", font=self.font_normal)
        self.url_entry.grid(row=0, column=0, sticky="ew")
        self._add_clear_button(url_row, self.url_entry, also_clear_thumbnail=True).grid(row=0, column=1, padx=(6, 6))
        ctk.CTkButton(url_row, text="Fetch info", width=100, font=self.font_normal,
                      command=self.fetch_info_clicked).grid(row=0, column=2)

        content_row = ctk.CTkFrame(tab, fg_color="transparent")
        content_row.grid(row=2, column=0, sticky="ew", padx=5, pady=(14, 0))
        content_row.grid_columnconfigure(0, weight=1)

        left = ctk.CTkFrame(content_row, fg_color="transparent")
        left.grid(row=0, column=0, sticky="ew")
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="Name of this download", font=self.font_label).grid(row=0, column=0, sticky="w")
        name_row = ctk.CTkFrame(left, fg_color="transparent")
        name_row.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        name_row.grid_columnconfigure(0, weight=1)
        self.name_entry = ctk.CTkEntry(name_row, placeholder_text="my_video", font=self.font_normal)
        self.name_entry.grid(row=0, column=0, sticky="ew")
        self._add_clear_button(name_row, self.name_entry, also_clear_thumbnail=True).grid(row=0, column=1, padx=(6, 0))

        self.thumbnail_label = ctk.CTkLabel(content_row, text="", width=160, height=90)
        self.thumbnail_label.grid(row=0, column=1, padx=(15, 0))

        btn_row = ctk.CTkFrame(tab, fg_color="transparent")
        btn_row.grid(row=3, column=0, sticky="ew", padx=5, pady=(16, 10))
        btn_row.grid_columnconfigure(0, weight=1)
        self.download_btn = ctk.CTkButton(btn_row, text="Download", font=self.font_label, height=40,
                                           command=self.start_single_download)
        self.download_btn.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(btn_row, text="Add last download to playlist", font=self.font_normal, height=40,
                      width=210, command=self.add_last_download_to_playlist).grid(row=0, column=1, padx=(10, 0))
        self.add_to_playlist_status_label = ctk.CTkLabel(tab, text="", font=self.font_small, anchor="w")
        self.add_to_playlist_status_label.grid(row=4, column=0, sticky="w", padx=5, pady=(0, 4))

    def _build_batch_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(tab, text="Paste one URL per line - filenames are auto-fetched from each title.",
                     font=self.font_small, text_color="gray60", anchor="w").grid(
            row=0, column=0, sticky="w", padx=5, pady=(10, 4))
        name_row = ctk.CTkFrame(tab, fg_color="transparent")
        name_row.grid(row=1, column=0, sticky="ew", padx=5, pady=(0, 6))
        name_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(name_row, text="Request name (optional):", font=self.font_small).grid(
            row=0, column=0, padx=(0, 8))
        self.queue_name_entry = ctk.CTkEntry(
            name_row, font=self.font_normal,
            placeholder_text="e.g. \"Weekend playlist\" - easier to find later in Requests/History")
        self.queue_name_entry.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        self._add_clear_button(name_row, self.queue_name_entry).grid(row=0, column=2)
        self.batch_box = ctk.CTkTextbox(tab, font=self.font_normal, height=90)
        self.batch_box.grid(row=2, column=0, sticky="nsew", padx=5, pady=(0, 8))

        # Dynamic URL list - built in the SAME grid cell as batch_box, so
        # toggling Settings > Advanced > "Use the dynamic URL list for
        # Batch Queue" just swaps which one is visible (see
        # _apply_batch_queue_mode) without needing to rebuild anything.
        self._batch_urls = []
        self._batch_undo_stack = []
        self._batch_dynamic_frame = ctk.CTkFrame(tab, fg_color="transparent")
        self._batch_dynamic_frame.grid(row=2, column=0, sticky="nsew", padx=5, pady=(0, 8))
        self._batch_dynamic_frame.grid_columnconfigure(0, weight=1)
        self._batch_dynamic_frame.grid_rowconfigure(2, weight=1)

        add_row = ctk.CTkFrame(self._batch_dynamic_frame, fg_color="transparent")
        add_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        add_row.grid_columnconfigure(0, weight=1)
        self.batch_add_entry = ctk.CTkEntry(add_row, font=self.font_normal,
                                             placeholder_text="Paste one or more URLs (one per line), press Enter")
        self.batch_add_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.batch_add_entry.bind("<Return>", lambda e: self._add_batch_urls_from_entry())
        ctk.CTkButton(add_row, text="Add", width=70, font=self.font_normal,
                      command=self._add_batch_urls_from_entry).grid(row=0, column=1)

        undo_row = ctk.CTkFrame(self._batch_dynamic_frame, fg_color="transparent")
        undo_row.grid(row=1, column=0, sticky="w", pady=(0, 6))
        self.batch_undo_btn = ctk.CTkButton(undo_row, text="Undo", width=70, font=self.font_small,
                                             fg_color="gray40", hover_color="gray30",
                                             command=self._undo_batch_url_removal, state="disabled")
        self.batch_undo_btn.pack(side="left", padx=(0, 8))
        self.batch_undo_count_label = ctk.CTkLabel(undo_row, text="", font=self.font_small, text_color="gray60")
        self.batch_undo_count_label.pack(side="left")

        self.batch_dynamic_list_frame = ctk.CTkScrollableFrame(self._batch_dynamic_frame)
        self.batch_dynamic_list_frame.grid(row=2, column=0, sticky="nsew")

        btn_row = ctk.CTkFrame(tab, fg_color="transparent")
        btn_row.grid(row=3, column=0, sticky="ew", padx=5, pady=(0, 10))
        self.batch_btn = ctk.CTkButton(btn_row, text="Start Queue", font=self.font_label, height=36,
                                        command=self.start_batch_download)
        self.batch_btn.pack(side="left", fill="x", expand=True)
        # Text reflects the current batch-queue mode - "X" for the
        # dynamic URL-list variant (per how this was specifically asked
        # for), "Clear" for the plain textbox variant. Kept in sync with
        # the mode by _apply_batch_queue_mode(), not just set once here.
        self.batch_clear_btn = ctk.CTkButton(btn_row, text="Clear", width=90, font=self.font_normal,
                                              fg_color="gray40", hover_color="gray30",
                                              command=self._clear_batch_queue)
        self.batch_clear_btn.pack(side="left", padx=(10, 0))
        self.batch_status_label = ctk.CTkLabel(tab, text="", font=self.font_small, anchor="w")
        self.batch_status_label.grid(row=4, column=0, sticky="w", padx=5, pady=(0, 4))
        self._apply_batch_queue_mode()
        # Ctrl+Z only acts on the batch URL list, and only while dynamic
        # mode is actually on - bound at the toplevel level (not just
        # this one entry) since the whole point is undoing a removal
        # click, which could happen with focus anywhere in this frame,
        # not just while typing in the add-URL entry.
        self.bind_all("<Control-z>", self._on_ctrl_z_pressed)

    def _on_ctrl_z_pressed(self, _event=None):
        if self.cfg.get("dynamic_batch_queue_enabled", False) and self.tabview.get() == "Download" \
                and getattr(self, "inner_tabview", None) and self.inner_tabview.get() == "Batch Queue":
            self._undo_batch_url_removal()

    def _apply_batch_queue_mode(self):
        """Shows whichever of the two batch-queue UIs matches the
        current setting, hiding the other - both live in the same grid
        cell, so this is just a visibility swap, nothing gets rebuilt.
        Also keeps the shared Clear/X button's label in sync with the
        mode - "X" for the dynamic URL-list variant specifically (per
        how this was asked for), "Clear" for the plain textbox one."""
        if self.cfg.get("dynamic_batch_queue_enabled", False):
            self.batch_box.grid_remove()
            self._batch_dynamic_frame.grid()
            if hasattr(self, "batch_clear_btn"):
                self.batch_clear_btn.configure(text="\u2715", width=32)
        else:
            self._batch_dynamic_frame.grid_remove()
            self.batch_box.grid()
            if hasattr(self, "batch_clear_btn"):
                self.batch_clear_btn.configure(text="Clear", width=90)

    def _on_dynamic_batch_queue_changed(self):
        self.cfg["dynamic_batch_queue_enabled"] = self.dynamic_batch_queue_var.get()
        save_config(self.cfg)
        self._apply_batch_queue_mode()

    def _clear_batch_queue(self):
        self.batch_box.delete("1.0", "end")
        self._batch_urls = []
        self._batch_undo_stack = []
        self._refresh_batch_dynamic_list()

    def _add_batch_urls_from_entry(self):
        """Parses whatever's in the add-URL entry - even a big block of
        newline-separated URLs pasted all at once - into individual
        entries in the dynamic list, per how this was specifically
        asked for (a paste of a longer list still gets split correctly,
        not treated as one single garbled "URL")."""
        raw = self.batch_add_entry.get()
        urls = [u.strip() for u in raw.splitlines() if u.strip()]
        if not urls:
            return
        self._batch_urls.extend(urls)
        self.batch_add_entry.delete(0, "end")
        self._refresh_batch_dynamic_list()

    def _remove_batch_url(self, index):
        """The red X next to a URL - removes it immediately, no
        confirmation prompt (per how this was specifically asked for,
        since it's undoable), but doesn't truly forget it until the
        download actually starts - see start_batch_download(), which
        clears _batch_undo_stack once a request is actually created."""
        if 0 <= index < len(self._batch_urls):
            url = self._batch_urls.pop(index)
            self._batch_undo_stack.append((index, url))
            self._refresh_batch_dynamic_list()

    def _undo_batch_url_removal(self):
        if not self._batch_undo_stack:
            return
        index, url = self._batch_undo_stack.pop()
        index = min(index, len(self._batch_urls))  # in case other removals shifted things since
        self._batch_urls.insert(index, url)
        self._refresh_batch_dynamic_list()

    def _refresh_batch_dynamic_list(self):
        for w in self.batch_dynamic_list_frame.winfo_children():
            w.destroy()
        if not self._batch_urls:
            ctk.CTkLabel(self.batch_dynamic_list_frame, text="No URLs yet - paste some above.",
                         font=self.font_small, text_color="gray60").pack(pady=10)
        for i, url in enumerate(self._batch_urls):
            row = ctk.CTkFrame(self.batch_dynamic_list_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkButton(row, text="\u2715", width=24, font=self.font_small, fg_color="#a13333",
                          hover_color="#7d2626", command=lambda i=i: self._remove_batch_url(i)).pack(
                side="left", padx=(0, 6))
            ctk.CTkLabel(row, text=url, font=self.font_small, text_color="gray60", anchor="w").pack(
                side="left", fill="x", expand=True)
        self.batch_undo_btn.configure(state="normal" if self._batch_undo_stack else "disabled")
        self.batch_undo_count_label.configure(
            text=f"{len(self._batch_undo_stack)} removed" if self._batch_undo_stack else "")

    def _add_clear_button(self, parent, entry_widget, also_clear_thumbnail=False):
        def do_clear():
            entry_widget.delete(0, "end")
            entry_widget.focus_set()  # also select/focus the field, so it's ready to type into right away
            # Only touch the thumbnail widget if there's actually an image
            # showing right now - calling configure(image=None) on a label
            # whose image was already cleared (or never set) raises
            # _tkinter.TclError: image "pyimageN" doesn't exist, because Tk
            # has already released the underlying PhotoImage once nothing
            # references it, and CTkLabel's configure() still tries to
            # reference that stale name.
            if also_clear_thumbnail and self.thumbnail_image is not None:
                self._clear_thumbnail()
        return ctk.CTkButton(parent, text="X", width=28, font=self.font_normal, fg_color="gray40",
                              hover_color="gray30", command=do_clear)

    @staticmethod
    def _clamp_drag_height(start_height, delta, min_height, max_height):
        """The actual resize math, pulled out as its own pure function
        so it's directly unit-testable without needing a real mouse drag
        (Xvfb's synthetic X11 events don't reliably carry realistic
        root-coordinate deltas for drag gestures, which makes testing
        the full gesture through event_generate unreliable in a headless
        environment - this keeps the part that actually matters testable
        regardless)."""
        return max(min_height, min(max_height, start_height + delta))

    def _add_search_clear_button(self, entry, string_var):
        """Adds a small 'X' button directly after a search entry that
        clears it - used consistently across every search bar in the
        app (Playlists, Library, General History, Request History)
        rather than each screen inventing its own. Packed into the same
        parent as `entry`, right after it - callers just build the
        entry normally and pass it here instead of also packing it
        themselves for this last step.

        Named distinctly from _add_clear_button (the Download tab's
        existing URL/Name/Output field clear-X, which uses grid() and a
        different signature) to avoid shadowing it - they're two
        different, unrelated widgets that happened to want a similar
        name."""
        ctk.CTkButton(entry.master, text="\u2715", width=28, font=self.font_small, fg_color="gray40",
                      hover_color="gray30", command=lambda: string_var.set("")).pack(side="left", padx=(4, 0))

    def _make_vertically_resizable(self, widget, min_height=60, max_height=800):
        """Adds a thin drag handle under a widget (a textbox, typically)
        that lets the user click-and-hold then drag to change its
        height live - for anything that doesn't already have a fixed,
        deliberately-locked height (unlike the Download tab's progress
        row, which is intentionally fixed for the opposite reason - see
        that section of this file). Returns the handle widget in case
        the caller wants to place/style it specially; normally it's
        enough that it's already packed directly below `widget`."""
        handle = ctk.CTkFrame(widget.master, height=6, fg_color="gray50", cursor="sb_v_double_arrow")
        handle.pack(fill="x", pady=(0, 8))
        drag_state = {"dragging": False, "start_y": 0, "start_height": 0}

        def on_press(event):
            drag_state["dragging"] = True
            drag_state["start_y"] = event.y_root
            drag_state["start_height"] = widget.winfo_height()

        def on_drag(event):
            if not drag_state["dragging"]:
                return
            delta = event.y_root - drag_state["start_y"]
            new_height = self._clamp_drag_height(drag_state["start_height"], delta, min_height, max_height)
            widget.configure(height=new_height)

        def on_release(_event):
            drag_state["dragging"] = False

        handle.bind("<ButtonPress-1>", on_press)
        handle.bind("<B1-Motion>", on_drag)
        handle.bind("<ButtonRelease-1>", on_release)
        handle._resize_test_hooks = (on_press, on_drag, on_release)  # exposed purely for direct testing
        return handle

    def _add_hint_icon(self, parent, text, wraplength=320):
        """A small, low-profile circular question-mark that shows the
        given explanatory text on hover OR click (click toggles it - for
        anyone who finds hover awkward, e.g. touch/trackpad users, or
        just wants it to stay up while they read) - this is what
        replaces the old pattern of an always-visible paragraph of
        description text sitting permanently under every setting, per
        how this was specifically asked for ("minimize what the user
        sees"). Returns the hint widget so the caller can pack/grid it
        inline next to whatever it's explaining."""
        hint = ctk.CTkLabel(parent, text="?", font=ctk.CTkFont(size=11, weight="bold"),
                             width=18, height=18, corner_radius=9, fg_color=("gray75", "gray30"),
                             text_color=("gray20", "gray85"), cursor="hand2")
        tip = {"win": None}

        def open_tip():
            if tip["win"] is not None:
                return
            x = hint.winfo_rootx()
            y = hint.winfo_rooty() + hint.winfo_height() + 4
            win = ctk.CTkToplevel(hint)
            win.overrideredirect(True)
            win.geometry(f"+{x}+{y}")
            win.attributes("-topmost", True)
            ctk.CTkLabel(win, text=text, font=self.font_small, fg_color=("gray85", "gray20"),
                        corner_radius=6, padx=10, pady=8, wraplength=wraplength, justify="left").pack()
            # Safety net so the popup can never get stuck open: leaving
            # the popup window itself also closes it, on top of leaving
            # the "?" icon doing the same - covers the edge case of the
            # mouse trajectory briefly crossing into the popup's own
            # small gap area, per "ensure they go away when the pointer
            # is not on them" applying to ALL question-mark tooltips,
            # not just the common case of leaving the icon directly.
            win.bind("<Leave>", lambda e: close_tip())
            tip["win"] = win

        def close_tip():
            if tip["win"] is not None:
                tip["win"].destroy()
                tip["win"] = None

        def toggle_tip(_event=None):
            close_tip() if tip["win"] is not None else open_tip()

        hint.bind("<Enter>", lambda e: open_tip())
        hint.bind("<Leave>", lambda e: close_tip())
        hint.bind("<Button-1>", toggle_tip)
        hint._hint_test_hooks = (open_tip, close_tip, toggle_tip, tip)  # exposed purely for direct testing
        return hint

    def _add_tooltip(self, widget, text):
        """A minimal hover tooltip - just enough to show a full name when
        the button itself is showing a truncated version."""
        tip = {"win": None}

        def show(_event=None):
            if tip["win"] is not None:
                return
            x = widget.winfo_rootx() + 10
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            win = ctk.CTkToplevel(widget)
            win.overrideredirect(True)
            win.geometry(f"+{x}+{y}")
            win.attributes("-topmost", True)
            ctk.CTkLabel(win, text=text, font=self.font_small, fg_color=("gray85", "gray20"),
                        corner_radius=4, padx=8, pady=4).pack()
            tip["win"] = win

        def hide(_event=None):
            if tip["win"] is not None:
                tip["win"].destroy()
                tip["win"] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    def _clear_thumbnail(self):
        if self.thumbnail_image is None:
            return
        try:
            self.thumbnail_label.configure(image=None, text="")
        except Exception:
            pass  # already gone - nothing more to do
        self.thumbnail_image = None

    def clear_log(self):
        """The disclaimer used to live at the top of this log and get
        re-inserted here to survive a clear - it's moved to the Extras
        tab now (always visible there, not something a log clear affects
        at all), so this is a plain clear. Also empties the backing
        _log_entries list, not just the visible textbox - otherwise
        switching log mode afterward would silently bring old lines back."""
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        self._log_entries = []

    # ------------------------------------------------------------------ #
    def _on_type_change(self, value):
        placeholder = "my_video" if value == "Video" else "my_song"
        self.name_entry.configure(placeholder_text=placeholder)
        self.subtitles_check.configure(state="normal" if value == "Video" else "disabled")
        # Switching between Video/Audio resets Full playlist and
        # Subtitles back to their configured defaults, rather than
        # leaving whatever was toggled on for the other type - carrying
        # "Full playlist" over from a video download into an audio one
        # (or vice versa) is rarely what's actually wanted.
        self.playlist_var.set(self.cfg.get("default_playlist", False))
        self.subtitles_var.set(self.cfg.get("default_subtitles", False))

    def _on_output_playlist_selected(self, value):
        if value == "Select a Playlist...":
            return  # the inert placeholder - does nothing, per how this was asked for
        folder = self._playlist_folder(value)
        if folder:
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, folder)

    def _refresh_output_playlist_dropdown(self):
        if not hasattr(self, "output_playlist_dropdown"):
            return
        names = ["Select a Playlist..."] + list_playlists(self.cfg.get("playlists_path", ""))
        self.output_playlist_dropdown.configure_values(names)

    def browse_output(self):
        directory = filedialog.askdirectory(title="Select output folder")
        if directory:
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, directory)

    def _resolve_output_dir(self):
        out_dir = self.output_entry.get().strip()
        if not out_dir:
            out_dir = self.cfg["video_path"] if self.type_var.get() == "Video" else self.cfg["music_path"]
        return out_dir

    def open_current_output_folder(self):
        target = self.last_output_dir or self._resolve_output_dir()
        if not target:
            messagebox.showwarning("No folder set", "Set a default download folder in Settings first.")
            return
        os.makedirs(target, exist_ok=True)
        if not open_folder(target):
            messagebox.showwarning("Not found", f"Could not open:\n{target}")

    def open_output_in_vlc(self):
        """Opens the most recently downloaded FILE in VLC if we have one,
        rather than just the containing folder - previously this always
        opened the folder, which from the user's perspective just looked
        like "clicking this button opens VLC and does nothing else." Falls
        back to the folder if nothing's been downloaded yet this session."""
        if self.last_downloaded_path and os.path.isfile(self.last_downloaded_path):
            ok, msg = open_in_vlc(self.last_downloaded_path)
        else:
            target = self.last_output_dir or self._resolve_output_dir()
            ok, msg = open_in_vlc(target)
        if not ok:
            messagebox.showwarning("VLC", msg)

    # ------------------------------------------------------------------ #
    def fetch_info_clicked(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Enter a URL first.")
            return
        self._log(f"Fetching info for {url} ...")
        threading.Thread(target=self._fetch_info_thread, args=(url,), daemon=True).start()

    def _fetch_info_thread(self, url):
        try:
            info = fetch_info(url)
            self.after(0, lambda: self._apply_fetched_info(info))
        except DownloadStageError as e:
            self.after(0, lambda: self._log(f"Could not fetch info ({e.stage}): {e.original}"))
        except Exception as e:
            self.after(0, lambda: self._log(f"Could not fetch info: {e}"))

    def _apply_fetched_info(self, info):
        title = sanitize_filename(beautify_title(info.get("title", "download")))
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, title)
        self._log(f"Title fetched: {title}")
        thumb_url = info.get("thumbnail")
        if thumb_url:
            threading.Thread(target=self._load_thumbnail, args=(thumb_url,), daemon=True).start()

    def _load_thumbnail(self, url):
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = resp.read()
            img = Image.open(io.BytesIO(data))
            img.thumbnail((160, 90))
            self.after(0, lambda: self._show_thumbnail(img))
        except Exception:
            pass

    def _show_thumbnail(self, img):
        new_image = ctk.CTkImage(img, size=img.size)
        try:
            self.thumbnail_label.configure(image=new_image, text="")
        except Exception:
            # See _thumbnail_image_history note in __init__ for why this
            # can happen even with a brand-new CTkImage. Retry once after
            # a full reset before giving up and just logging it.
            try:
                self.thumbnail_label.configure(image=None, text="")
                self.thumbnail_label.configure(image=new_image, text="")
            except Exception as e:
                self._log(f"Thumbnail preview couldn't be displayed: {e}")
                return
        self.thumbnail_image = new_image
        self._thumbnail_image_history.append(new_image)
        if len(self._thumbnail_image_history) > 30:
            del self._thumbnail_image_history[:-30]

    # ------------------------------------------------------------------ #
    LOG_LEVELS = {"simple": 0, "detailed": 1, "developer": 2}
    LOG_COLORS = {"green": "#2fa84f", "red": "#c0392b", "blue": "#3b8ed0"}

    STATUS_COLORS = {"success": "#2fa84f", "info": "gray60", "neutral": "gray60"}

    def _apply_tab_icon(self, tab_name):
        """Applies the shared, consistently-sized icon for one sidebar
        tab, if it exists in TAB_ICONS and the tab itself currently
        exists in the sidebar - safe to call for Developer before it's
        been created (silently does nothing) as well as after."""
        icon_rel_path = TAB_ICONS.get(tab_name)
        if not icon_rel_path or tab_name not in self.tabview.buttons_dict:
            return
        try:
            icon_img = ctk.CTkImage(Image.open(resource_path(icon_rel_path)), size=TAB_ICON_SIZE)
            self.tabview.buttons_dict[tab_name].configure(image=icon_img, compound="left")
        except Exception:
            pass

    def _section_header(self, parent, text):
        """A section header styled to actually read as a distinct block -
        not just bold text sitting in a long list, which is what these
        looked like before. Larger font (font_section), a colored accent
        bar matching whatever color theme is currently active (so it
        stays consistent across all 12 theme options rather than a
        hardcoded color), and a full-width divider underneath with
        generous spacing above - used for every category-style header
        across Settings, More, and the Developer tab."""
        wrapper = ctk.CTkFrame(parent, fg_color="transparent")
        wrapper.pack(fill="x", pady=(22, 2))
        try:
            accent_color = ctk.ThemeManager.theme["CTkButton"]["fg_color"]
        except Exception:
            accent_color = ("#3B8ED0", "#1F6AA5")  # customtkinter's own default blue, as a safe fallback
        # An explicit height here, NOT fill="y" - the wrapper frame has no
        # height of its own to fill against (it's only packed with
        # fill="x"), so fill="y" on the bar stretched it to whatever
        # arbitrary height the geometry manager happened to resolve,
        # spilling down across unrelated content below. A fixed height
        # matched to roughly one line of the header font is what was
        # actually wanted - a short accent mark next to the text, not a
        # tall bar.
        bar_height = self.font_section.cget("size") + 10
        ctk.CTkFrame(wrapper, width=4, height=bar_height, fg_color=accent_color, corner_radius=2).pack(
            side="left", padx=(0, 8))
        ctk.CTkLabel(wrapper, text=text, font=self.font_section, anchor="w").pack(side="left")
        ctk.CTkFrame(parent, height=1, fg_color=("gray75", "gray30")).pack(fill="x", pady=(6, 10))
        return wrapper

    def _sub_header(self, parent, text, pack_side="top"):
        """A heading nested under a _section_header - Settings' Main
        Header -> subheader -> options structure. Bigger than ordinary
        text (font_subsection) and bold, but clearly a level below the
        main category headers (font_section) - no leading "-" prefix
        (removed per how this was asked for), the size difference alone
        now carries the hierarchy. pack_side="left" is for the header-
        plus-hint-icon row pattern (a small dedicated frame holding just
        this label and a hint icon side by side) - default "top" is
        unchanged for every other, more common call site where this is
        packed directly into a large vertically-stacking container."""
        ctk.CTkLabel(parent, text=text, font=self.font_subsection, anchor="w").pack(
            side=pack_side, anchor="w", padx=5, pady=(14, 6))

    def _set_inline_status(self, label_widget, message, kind="info", clear_after_ms=6000):
        """Puts a short status message directly next to whatever button
        triggered it, instead of a blocking 'OK' popup - used everywhere
        a showinfo() used to be. Only real problems (showerror /
        showwarning, the yellow-triangle ones) still interrupt as popups;
        anything merely informational shows up inline where it's relevant
        and fades on its own after a few seconds."""
        color = self.STATUS_COLORS.get(kind, "gray60")
        label_widget.configure(text=message, text_color=color)
        if clear_after_ms:
            label_widget.after(clear_after_ms, lambda: label_widget.configure(text="")
                                if label_widget.cget("text") == message else None)

    def _log(self, message, level="simple", color=None):
        """Every log line is tagged with a level (simple/detailed/developer)
        and kept in self._log_entries regardless of the CURRENT display
        mode - switching the mode dropdown later just re-renders from this
        backing list, nothing is lost by being filtered out at the time.
        color, when given, is one of 'green'/'red'/'blue' (see LOG_COLORS)
        - completed downloads are green, errors red, active downloads blue,
        per how this was asked for. Still recorded to the backing list
        even while the log display is turned off, so switching it back on
        shows what happened while it was off, rather than a gap."""
        entry = (level, message, color)
        self._log_entries.append(entry)
        if hasattr(self, "mini_log_label"):
            mini_color = self.LOG_COLORS.get(color, "gray60")
            self._last_mini_log_text = message
            if self.tabview.get() != "Download":
                self.mini_log_label.configure(text=message, text_color=mini_color)
        if not getattr(self, "log_enabled_var", None) or not self.log_enabled_var.get():
            return
        if self.LOG_LEVELS.get(level, 0) <= self.LOG_LEVELS.get(self._log_mode_var.get().lower(), 0):
            self._append_log_line(message, color)

    def _on_log_enabled_changed(self):
        if self.log_enabled_var.get():
            self.log_disabled_placeholder.grid_remove()
            self.log_box.grid()
            # Catch the display up on whatever was logged while it was
            # off, rather than just resuming from here with a gap.
            self._on_log_mode_changed()
        else:
            self.log_box.grid_remove()
            self.log_disabled_placeholder.grid()

    def _append_log_line(self, message, color):
        self.log_box.configure(state="normal")
        if color and color in self.LOG_COLORS:
            tag = f"color_{color}"
            if tag not in self.log_box.tag_names():
                self.log_box.tag_config(tag, foreground=self.LOG_COLORS[color])
            self.log_box.insert("end", message + "\n", tag)
        else:
            self.log_box.insert("end", message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        # Mirrors the same line (and color, when it has one) into the
        # one-line mini log echo shown on non-Download tabs. Only
        # actually visually updated when NOT on the Download tab
        # (which already has the real log right there) - _last_mini_log_text
        # is still tracked either way, so switching to another tab
        # later shows the truly most-recent message, not a stale one.
        if hasattr(self, "mini_log_label"):
            mini_color = self.LOG_COLORS.get(color, "gray60")
            self._last_mini_log_text = message
            if self.tabview.get() != "Download":
                self.mini_log_label.configure(text=message, text_color=mini_color)

    def _prompt_dev_feature_redirect(self, feature_description="This feature"):
        """Gate for anything that needs a Developer-tab feature toggle
        enabled, from OUTSIDE the Developer tab. Branches on whether the
        user is already logged in: not logged in -> offer the login
        redirect (_prompt_dev_login_redirect); already logged in (just
        hasn't flipped this particular toggle yet) -> redirecting to a
        login form would be wrong, so this takes them straight to the
        Developer tab itself instead, where the toggle actually lives."""
        if not getattr(self, "_dev_authenticated", False):
            self._prompt_dev_login_redirect(feature_description)
            return
        if messagebox.askyesno("Developer feature not enabled",
                                f"{feature_description} isn't enabled yet. Turn it on in the Developer "
                                f"tab's feature toggles.\n\nGo there now?"):
            self._open_developer_tab()

    def _prompt_dev_login_redirect(self, feature_description="This feature"):
        """Called whenever the user tries to use something that requires
        developer access from OUTSIDE the Developer tab (e.g. picking
        "Developer" in a log/display mode dropdown while not logged
        in). Rather than just telling them to go find the login form
        themselves, offers to take them straight there: switches to
        More > Information, opens the (normally collapsed) login area,
        and focuses the username field ready to type. Returns True if
        the user said yes and the redirect happened, False otherwise -
        callers use this to know whether to also revert whatever UI
        state triggered the prompt (e.g. resetting a dropdown back to
        its previous value)."""
        if not messagebox.askyesno("Developer login required",
                                    f"{feature_description} requires being logged in as a developer.\n\n"
                                    f"Would you like to log in now?"):
            return False
        self.tabview.set("More")
        if hasattr(self, "more_tabview"):
            self.more_tabview.set("Information")
        if hasattr(self, "_dev_login_area") and not self._dev_login_area.winfo_ismapped():
            self._dev_login_area.pack(anchor="w", pady=(0, 15))
        if hasattr(self, "dev_user_entry"):
            self.dev_user_entry.focus_set()
        return True

    def _on_log_mode_changed(self, _value=None):
        """Re-renders the whole log from self._log_entries at the newly
        selected mode, rather than only affecting future lines. 'Developer'
        specifically requires the dev feature toggle to be on (checked
        here, at selection time, not by hiding it from the dropdown) -
        same gating pattern as Request History's display mode."""
        if self._log_mode_var.get() == "Developer" and not self.cfg.get("dev_log_mode_enabled", False):
            self._prompt_dev_feature_redirect("Developer log mode")
            self._log_mode_var.set("Simple")
            return
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        current_level = self.LOG_LEVELS.get(self._log_mode_var.get().lower(), 0)
        for level, message, color in self._log_entries:
            if self.LOG_LEVELS.get(level, 0) <= current_level:
                self._append_log_line(message, color)

    @staticmethod
    def _format_eta(seconds):
        """Turns a raw seconds count into a short human string - "45s",
        "3m 12s", "1h 05m" - used for both the per-item and whole-queue
        time estimates. None/negative/absurdly large values (a stalled
        connection can make speed-based math blow up briefly) return
        None so callers can just skip showing anything rather than
        printing something misleading."""
        if seconds is None or seconds < 0 or seconds > 100000:
            return None
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        minutes, secs = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {secs:02d}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes:02d}m"

    def _set_progress(self, pct, speed):
        self.progress_bar.set(pct)
        self.mini_progress_bar.set(pct)
        self._last_progress_pct = pct
        # Speed is no longer written into the label here on every raw
        # hook call - see _tick_speed_display, which reads the smoothed
        # 5-second rolling average on its own genuine 1-second cadence
        # instead, per how this was specifically asked for ("updated
        # every 1 second... reflects the average of the last 5
        # seconds"). The percentage still updates as often as the hook
        # fires, since that's an exact value with no reason to throttle
        # or smooth it the way a fluctuating speed reading needs.
        if not getattr(self, "_speed_tick_active", False):
            # No active 1-second ticker (e.g. between hook calls right
            # at the very start of a download) - still show SOMETHING
            # reasonable rather than a stale/blank label until the
            # ticker's first tick fires.
            speed_str = f" - {speed / 1024 / 1024:.2f} MB/s" if speed else ""
            self.progress_label.configure(text=f"{pct * 100:.1f}%{speed_str}")

    def _start_speed_display_tick(self):
        self._speed_tick_active = True
        self._tick_speed_display()

    def _stop_speed_display_tick(self):
        self._speed_tick_active = False

    def _tick_speed_display(self):
        """Runs once a second for the duration of an active download -
        reads the smoothed 5-second rolling average speed (see
        core/speed_tracker.py) rather than whatever the raw progress
        hook's last instant reading happened to be, and updates both the
        displayed speed AND the ETA from that same smoothed value (ETA
        itself is already computed from the smoothed average inside
        core/downloader.py's _hook - this just re-renders the label to
        match, on this same 1-second cadence)."""
        if not getattr(self, "_speed_tick_active", False):
            return
        pct = getattr(self, "_last_progress_pct", 0)
        smoothed_speed = self.downloader.speed_tracker.get_average(window_seconds=5) if self.downloader else None
        speed_str = f" - {smoothed_speed / 1024 / 1024:.2f} MB/s" if smoothed_speed else ""
        self.progress_label.configure(text=f"{pct * 100:.1f}%{speed_str}")
        self._update_eta_label()
        self.after(1000, self._tick_speed_display)

    def _update_eta_label(self):
        """Builds the combined ETA text: this item's remaining time (from
        the downloader's own live progress hook) and, if a batch/playlist
        run is in progress with completed-item history to estimate from,
        the whole queue's remaining time too."""
        parts = []
        item_eta = self._format_eta(self.downloader.last_eta_seconds) if self.downloader else None
        if item_eta:
            parts.append(f"~{item_eta} left")

        durations = getattr(self, "_batch_item_durations", None)
        remaining_items = getattr(self, "_batch_items_remaining", 0)
        if durations and remaining_items > 0:
            avg = sum(durations) / len(durations)
            queue_eta_seconds = avg * (remaining_items - 1) + (self.downloader.last_eta_seconds or avg
                                                                 if self.downloader else avg)
            queue_eta = self._format_eta(queue_eta_seconds)
            if queue_eta:
                parts.append(f"~{queue_eta} for whole queue")

        self.eta_label.configure(text=" | ".join(parts))

    def _on_main_tab_changed(self):
        """Shows the compact header progress bar + one-line log echo on
        every tab except Download (which already has the real versions
        of both right there), and closes any open ScrollableDropdown
        popup - those are separate Toplevel windows, not part of any one
        tab's frame, so a dropdown left open on the tab being left would
        otherwise stay floating on top of whatever tab gets switched to.

        A real, confirmed performance/artifact bug lived here: this used
        to call grid_remove()/grid() on mini_progress_bar and
        mini_log_label, which doesn't just hide them - it removes the
        row from the grid's layout ENTIRELY, so everything below it
        (the whole sidebar + content area) had to physically shift up
        or down by that row's height on every single tab switch. Now
        both stay gridded (their row space permanently reserved) at all
        times - only their visible CONTENT changes with the current
        tab, which is all that was ever actually needed."""
        from gui.scrollable_dropdown import ScrollableDropdown
        ScrollableDropdown.close_all()
        if not hasattr(self, "mini_progress_bar"):
            return
        # The progress bar itself is left alone entirely now (always
        # gridded, always mirroring the real value - see _set_progress)
        # since CTkProgressBar doesn't support a transparent color to
        # visually hide it anyway; only the log echo's text is
        # conditionally shown, which is a safe, simple string toggle.
        if self.tabview.get() == "Download":
            self.mini_log_label.configure(text="")
        else:
            self.mini_log_label.configure(text=getattr(self, "_last_mini_log_text", ""))

    # ------------------------------------------------------------------ #
    def start_single_download(self):
        url = self.url_entry.get().strip()
        name = self.name_entry.get().strip()
        dtype = self.type_var.get()

        if not url:
            messagebox.showwarning("Missing URL", "Please enter a URL.")
            return
        # Name is no longer required - if left blank, _run_single fetches
        # the video's own title and uses that instead.

        out_dir = self._resolve_output_dir()
        if not out_dir:
            if not self._ensure_download_root_for_download():
                self._log("Download did not start: no output folder was chosen.")
                return
            out_dir = self._resolve_output_dir()
            if not out_dir:
                messagebox.showerror("No download folder", "No output folder is available.")
                self._log("Download did not start: no output folder configured.")
                return

        ffmpeg_ok, _ = deps.check_ffmpeg()
        if not ffmpeg_ok:
            messagebox.showerror("FFmpeg missing",
                                  "FFmpeg isn't installed. Go to the Version tab and click Install next to FFmpeg.")
            self._log("Download did not start: FFmpeg is not installed.")
            return

        os.makedirs(out_dir, exist_ok=True)
        self.last_output_dir = out_dir

        self._set_downloading_state(True)
        threading.Thread(target=self._run_single, args=(dtype, url, name, out_dir), daemon=True).start()

    def _handle_bot_detection(self, request_id, url):
        """YouTube's 'Sign in to confirm you're not a bot' error - shown
        once, in plain language, as a yellow warning popup (not a plain
        error) plus the same explanation in the log, and the caller
        stops the whole batch/queue right here rather than continuing to
        the next URL and hitting the identical wall over and over -
        that's what would actually flood the log for no benefit, since
        retrying or moving on doesn't fix a bot challenge."""
        msg = (
            "YouTube is asking to confirm you're not a bot, most often after downloading "
            "several videos in a row. Retrying won't fix this by itself.\n\n"
            "What helps:\n"
            "1. In Settings, set 'Cookies from browser' to the browser you're signed into "
            "YouTube with (Chrome, Firefox, Edge, etc). This is the most reliable fix.\n"
            "2. Wait a while before downloading again.\n"
            "3. Increase the 'Delay between batch/playlist items' setting to space out "
            "requests and avoid triggering this in the first place.\n\n"
            "The current download has been stopped so this doesn't repeat for every "
            "remaining item."
        )
        self._threadsafe_log("YouTube bot-detection triggered - see the popup for how to fix this. "
                              "Stopping this download to avoid repeating the same failure.", color="red")
        update_item(request_id, url, status="failed", error="YouTube bot detection (see popup)")
        self.after(0, lambda: messagebox.showwarning("YouTube is asking to verify you're not a bot", msg))

    def _handle_cookie_access_error(self, request_id, url):
        """yt-dlp couldn't read cookies from the chosen browser - almost
        always because that browser (most commonly Chrome) was open at
        the time and had its cookie database locked (yt-dlp issue #7271).
        Same treatment as bot detection: yellow popup with the actual
        fix, logged once, and the whole batch/queue stops here rather
        than repeating the identical failure for every remaining item."""
        browser = self.cfg.get("cookies_from_browser", "chrome")
        msg = (
            f"Couldn't read cookies from {browser.title()} - its cookie database is usually locked "
            f"while the browser is running, which is what's happening here.\n\n"
            "What helps:\n"
            f"1. Close {browser.title()} completely, then try the download again.\n"
            "2. Or switch 'Cookies from browser' in Settings to a different browser you also use.\n"
            "3. Or set it to 'None' if you don't need cookies for this download (bot-detection "
            "protection will be off, though).\n\n"
            "The current download has been stopped so this doesn't repeat for every remaining item."
        )
        self._threadsafe_log(f"Could not access {browser.title()} cookies (is it still open?) - see the "
                              f"popup for how to fix this. Stopping this download.", color="red")
        update_item(request_id, url, status="failed", error=f"Could not read {browser} cookies (see popup)")
        self.after(0, lambda: messagebox.showwarning("Couldn't read browser cookies", msg))

    def _run_single(self, dtype, url, name, out_dir):
        # Full-playlist mode gets its own dedicated path: fetch the
        # playlist's own info first, auto-create a playlist named after
        # it (or its first item if it has no title of its own), and
        # download every entry into that playlist's folder instead of
        # wherever the output field pointed.
        if self.playlist_var.get():
            self._run_playlist(dtype, url, out_dir)
            return

        request_id = start_request(dtype, "single", [url], out_dir=out_dir)
        update_item(request_id, url, status="downloading")
        # Created immediately (URL as a placeholder name until the real
        # title is known) rather than only at the very end - this is
        # what makes General History show "Analyzing" the instant a
        # download starts, updated in place through its real stages
        # (In Progress -> Success/Failed) rather than only ever showing
        # up once everything's already finished.
        history_entry_id = add_entry(url, url, dtype, "", "Analyzing")
        self.after(0, self._refresh_history_tab)

        if not name:
            self._threadsafe_log("No name given - fetching the video's title to use instead...")
            try:
                info = fetch_info(url)
                name = sanitize_filename(beautify_title(info.get("title", "download")))
                self._threadsafe_log(f"Using fetched title: {name}")
            except DownloadStageError as e:
                name = "download"
                self._threadsafe_log(f"Could not fetch title ({e.stage}): {e.original} - using 'download' instead")
            except Exception as e:
                name = "download"
                self._threadsafe_log(f"Could not fetch title: {e} - using 'download' instead")
        else:
            name = sanitize_filename(name)

        ext = self.cfg["video_format"] if dtype == "Video" else self.cfg["audio_format"]
        unique_name = make_unique_name(out_dir, name, ext)
        if unique_name != name:
            self._threadsafe_log(f"'{name}.{ext}' already exists - saving as '{unique_name}.{ext}' instead.")
        update_item(request_id, url, name=unique_name)
        update_entry(history_entry_id, name=unique_name)
        self.after(0, self._refresh_history_tab)

        # Duplicate check (Settings > Advanced) - skipped entirely for
        # retries (that's a separate code path in gui/request_history.py
        # that never calls this), since a retry is a deliberate
        # re-attempt, not something that should be silently blocked by
        # the very check meant to avoid redundant downloads.
        if self.cfg.get("duplicate_detection_enabled", True):
            existing = find_previous_download(url, out_dir)
            if existing:
                self._threadsafe_log(
                    f"Skipped - already downloaded to {existing['path']}. "
                    f"Turn off duplicate detection in Settings > Advanced if you want it anyway.",
                    color="blue")
                update_item(request_id, url, status="skipped", path=existing["path"])
                update_entry(history_entry_id, path=existing["path"], status="Skipped (duplicate)")
                self.after(0, self._refresh_history_tab)
                finish_request(request_id)
                self.after(0, lambda: self._set_downloading_state(False))
                self.after(0, self._refresh_history_tab)
                self.after(0, self._refresh_requests_tab)
                return

        self.downloader = Downloader(progress_callback=self._threadsafe_progress, log_callback=self._threadsafe_log,
                                          ping_ms_provider=lambda: self._network_ping)
        status, path, error_msg, was_cancelled = "Success", "", None, False
        update_entry(history_entry_id, status="In Progress")
        self.after(0, self._refresh_history_tab)
        try:
            if dtype == "Video":
                self._threadsafe_log(f'Downloading "{unique_name}"', color="blue")
                path = download_with_retry(
                    self.downloader.download_video, log_callback=self._threadsafe_log,
                    url=url, name=unique_name, out_dir=out_dir,
                    quality_key=self.cfg["video_quality"], fmt=self.cfg["video_format"],
                    playlist=False, subtitles=self.subtitles_var.get(), aspect_ratio=self.aspect_var.get(),
                    cookies_from_browser=self.cfg.get("cookies_from_browser", "none")
                )
            else:
                self._threadsafe_log(f'Downloading "{unique_name}"', color="blue")
                path = download_with_retry(
                    self.downloader.download_audio, log_callback=self._threadsafe_log,
                    url=url, name=unique_name, out_dir=out_dir,
                    quality=self.cfg["audio_quality"], fmt=self.cfg["audio_format"],
                    playlist=False, embed_thumbnail=self.cfg.get("embed_thumbnail", True),
                    cookies_from_browser=self.cfg.get("cookies_from_browser", "none")
                )
            self._threadsafe_log(f"Done: {path}", color="green")
            self._threadsafe_progress(1.0, None)
            self.last_downloaded_path = path
            update_item(request_id, url, status="success", path=path,
                        elapsed_seconds=self.downloader.elapsed_seconds())
        except DownloadCancelled:
            self._threadsafe_log("Download cancelled.")
            status = "Cancelled"
            was_cancelled = True
            removed, still_locked = cleanup_partial_files(out_dir, unique_name)
            if removed:
                self._threadsafe_log(f"Removed {len(removed)} partial file(s) from the cancelled download.")
            if still_locked:
                self._threadsafe_log(f"{len(still_locked)} partial file(s) are still in use by another "
                                      f"process and couldn't be removed - you may need to delete them "
                                      f"manually from {out_dir}.", color="red")
            update_item(request_id, url, status="failed", error="Cancelled by user",
                        elapsed_seconds=self.downloader.elapsed_seconds())
        except YouTubeBotDetectedError:
            status = "Failed"
            was_cancelled = True  # reuse this flag to skip the generic error popup - the bot-detection one covers it
            self._handle_bot_detection(request_id, url)
        except CookieAccessError:
            status = "Failed"
            was_cancelled = True  # same reasoning - the cookie-error popup covers it, skip the generic one
            self._handle_cookie_access_error(request_id, url)
        except DownloadStageError as e:
            status = "Failed"
            error_msg = f"Failed during {e.stage}: {e.original}"
            self._threadsafe_log(error_msg, color="red")
            update_item(request_id, url, status="failed", error=error_msg,
                        elapsed_seconds=self.downloader.elapsed_seconds())
        except Exception as e:
            status = "Failed"
            error_msg = f"Unexpected error: {e}"
            self._threadsafe_log(error_msg, color="red")
            update_item(request_id, url, status="failed", error=error_msg,
                        elapsed_seconds=self.downloader.elapsed_seconds())
        finally:
            update_entry(history_entry_id, name=unique_name, path=path, status=status)
            self.after(0, self._refresh_history_tab)
            finish_request(request_id)
            self.after(0, lambda: self._set_downloading_state(False))
            if error_msg and not was_cancelled:
                self.after(0, lambda: show_error("Download failed", error_msg, parent=self))
            self.after(0, self._refresh_history_tab)
            self.after(0, self._refresh_requests_tab)

    def _run_playlist(self, dtype, url, fallback_out_dir):
        """Full-playlist mode: look up the playlist's own title (or its
        first entry's title, if the playlist itself has none), create a
        new playlist with that name, and download every entry straight
        into that playlist's folder."""
        self._threadsafe_log("Full playlist mode - looking up playlist info...")
        timeout_s = self.cfg.get("playlist_fetch_timeout_s", 60)
        try:
            info = fetch_playlist_info(url, timeout_seconds=timeout_s)
        except PlaylistFetchTimeout as e:
            self._threadsafe_log(str(e), color="red")
            self.after(0, lambda: self._set_downloading_state(False))
            self.after(0, lambda: show_error("Playlist lookup timed out", str(e), parent=self))
            return
        except DownloadStageError as e:
            self._threadsafe_log(f"Could not read playlist ({e.stage}): {e.original}")
            self.after(0, lambda: self._set_downloading_state(False))
            self.after(0, lambda: show_error("Playlist lookup failed", str(e.original), parent=self))
            return

        root = ensure_playlists_root(self.cfg.get("playlists_path") or fallback_out_dir)
        playlist_name, _ = create_playlist(root, info["playlist_title"])
        if not playlist_name:
            # Name collision - fall back to a numbered variant rather than
            # failing the whole download outright.
            import time as _time
            playlist_name, _ = create_playlist(root, f"{info['playlist_title']} ({int(_time.time())})")
        playlist_out_dir = self._playlist_folder(playlist_name) or fallback_out_dir
        self._threadsafe_log(f"Created playlist '{playlist_name}' - downloading {len(info['entries']) or 1} item(s) into it.")
        self.after(0, self._refresh_playlists_tab)

        entry_urls = [e["url"] for e in info["entries"]] or [url]
        request_id = start_request(dtype, "playlist", entry_urls, out_dir=playlist_out_dir)

        delay_s = max(0, self.cfg.get("batch_delay_seconds", 0))
        # Same fix as _run_batch - clear any stale downloader reference
        # from a previous (possibly cancelled) run before this loop's
        # first cancel-check, so a retry doesn't immediately break out
        # thinking it's still cancelled.
        self.downloader = None
        self._batch_item_durations = []
        self._batch_items_remaining = len(entry_urls)
        for i, entry_url in enumerate(entry_urls, start=1):
            if self.downloader and self.downloader._cancel:
                break
            update_item(request_id, entry_url, status="downloading")
            self.after(0, lambda i=i, t=len(entry_urls): self.queue_progress_label.configure(text=f"Playlist item {i}/{t}"))
            self.downloader = Downloader(progress_callback=self._threadsafe_progress, log_callback=self._threadsafe_log,
                                          ping_ms_provider=lambda: self._network_ping)
            history_entry_id = add_entry(entry_url, entry_url, dtype, "", "Analyzing")
            self.after(0, self._refresh_history_tab)
            entry_media_info = None
            try:
                entry_media_info = fetch_media_info(entry_url)
                entry_name = sanitize_filename(beautify_title(entry_media_info.get("title", f"item_{i}")))
            except Exception:
                entry_name = f"item_{i}"
            ext = self.cfg["video_format"] if dtype == "Video" else self.cfg["audio_format"]
            unique_name = make_unique_name(playlist_out_dir, entry_name, ext)
            update_item(request_id, entry_url, name=unique_name)
            update_entry(history_entry_id, name=unique_name)
            self.after(0, self._refresh_history_tab)

            if self.cfg.get("duplicate_detection_enabled", True):
                existing = find_previous_download(entry_url, playlist_out_dir)
                if existing:
                    self._threadsafe_log(f"Skipped item {i}/{len(entry_urls)} - already downloaded to "
                                          f"{existing['path']}.", color="blue")
                    update_item(request_id, entry_url, status="skipped", path=existing["path"])
                    update_entry(history_entry_id, path=existing["path"], status="Skipped (duplicate)")
                    self.after(0, self._refresh_history_tab)
                    self._batch_items_remaining = max(0, self._batch_items_remaining - 1)
                    continue

            status, path = "Success", ""
            update_entry(history_entry_id, status="In Progress")
            self.after(0, self._refresh_history_tab)
            try:
                if dtype == "Video":
                    self._threadsafe_log(f'Downloading "{unique_name}"', color="blue")
                    path = download_with_retry(
                        self.downloader.download_video, log_callback=self._threadsafe_log,
                        url=entry_url, name=unique_name, out_dir=playlist_out_dir,
                        quality_key=self.cfg["video_quality"], fmt=self.cfg["video_format"],
                        playlist=False, subtitles=self.subtitles_var.get(), aspect_ratio=self.aspect_var.get(),
                        cookies_from_browser=self.cfg.get("cookies_from_browser", "none"),
                        prefetched_info=entry_media_info
                    )
                else:
                    self._threadsafe_log(f'Downloading "{unique_name}"', color="blue")
                    path = download_with_retry(
                        self.downloader.download_audio, log_callback=self._threadsafe_log,
                        url=entry_url, name=unique_name, out_dir=playlist_out_dir,
                        quality=self.cfg["audio_quality"], fmt=self.cfg["audio_format"],
                        playlist=False, embed_thumbnail=self.cfg.get("embed_thumbnail", True),
                        cookies_from_browser=self.cfg.get("cookies_from_browser", "none")
                    )
                self._threadsafe_log(f"Saved: {path}")
                # No separate "add to playlist" step needed - path is
                # already inside playlist_out_dir, which IS the playlist
                # folder now that playlists are filesystem-based.
                update_item(request_id, entry_url, status="success", path=path,
                            elapsed_seconds=self.downloader.elapsed_seconds())
            except DownloadCancelled:
                self._threadsafe_log("Playlist download cancelled.")
                removed, still_locked = cleanup_partial_files(playlist_out_dir, unique_name)
                if removed:
                    self._threadsafe_log(f"Removed {len(removed)} partial file(s).")
                if still_locked:
                    self._threadsafe_log(f"{len(still_locked)} partial file(s) still in use, couldn't be "
                                          f"removed - you may need to delete them manually.", color="red")
                update_item(request_id, entry_url, status="failed", error="Cancelled by user")
                status = "Cancelled"
                update_entry(history_entry_id, path=path, status=status)
                self.after(0, self._refresh_history_tab)
                break
            except YouTubeBotDetectedError:
                status = "Failed"
                self._handle_bot_detection(request_id, entry_url)
                update_entry(history_entry_id, path=path, status=status)
                self.after(0, self._refresh_history_tab)
                break  # stop the whole playlist, not just this item
            except CookieAccessError:
                status = "Failed"
                self._handle_cookie_access_error(request_id, entry_url)
                update_entry(history_entry_id, path=path, status=status)
                self.after(0, self._refresh_history_tab)
                break
            except DownloadStageError as e:
                status = "Failed"
                err = f"Failed during {e.stage}: {e.original}"
                self._threadsafe_log(err, color="red")
                update_item(request_id, entry_url, status="failed", error=err)
            except Exception as e:
                status = "Failed"
                err = f"Unexpected error: {e}"
                self._threadsafe_log(err, color="red")
                update_item(request_id, entry_url, status="failed", error=str(e))
            update_entry(history_entry_id, name=unique_name, path=path, status=status)
            self.after(0, self._refresh_history_tab)
            self._batch_items_remaining = max(0, self._batch_items_remaining - 1)
            if status == "Success":
                self._batch_item_durations.append(self.downloader.elapsed_seconds())

            if delay_s and i < len(entry_urls) and not (self.downloader and self.downloader._cancel):
                waited = 0.0
                while waited < delay_s:
                    if self.downloader and self.downloader._cancel:
                        break
                    time.sleep(min(0.25, delay_s - waited))
                    waited += 0.25

        finish_request(request_id)
        self._threadsafe_log("Playlist download finished.", color="green")
        self.after(0, lambda: self._set_downloading_state(False))
        self.after(0, self._refresh_history_tab)
        self.after(0, self._refresh_requests_tab)
        self.after(0, self._refresh_playlists_tab)

    # ------------------------------------------------------------------ #
    def start_batch_download(self):
        if self.cfg.get("dynamic_batch_queue_enabled", False):
            urls = list(self._batch_urls)
        else:
            raw = self.batch_box.get("1.0", "end").strip()
            urls = [u.strip() for u in raw.splitlines() if u.strip()]
        if not urls:
            messagebox.showwarning("Empty queue", "Paste at least one URL.")
            return
        if self.batch_running:
            self._set_inline_status(self.batch_status_label, "A batch is already running.", "info")
            return

        out_dir = self._resolve_output_dir()
        if not out_dir:
            if not self._ensure_download_root_for_download():
                self._log("Batch queue did not start: no output folder was chosen.")
                return
            out_dir = self._resolve_output_dir()
            if not out_dir:
                messagebox.showerror("No download folder", "No output folder is available.")
                return

        ffmpeg_ok, _ = deps.check_ffmpeg()
        if not ffmpeg_ok:
            messagebox.showerror("FFmpeg missing", "Install FFmpeg from the Version tab first.")
            return

        os.makedirs(out_dir, exist_ok=True)
        self.last_output_dir = out_dir
        self.batch_running = True
        self._set_downloading_state(True, batch=True)
        custom_name = self.queue_name_entry.get().strip() or None
        if self.cfg.get("dynamic_batch_queue_enabled", False):
            # The request is genuinely being created now - this is the
            # point past which a removed URL is no longer recoverable,
            # per how this was specifically asked for.
            self._batch_undo_stack = []
            self._refresh_batch_dynamic_list()
        threading.Thread(target=self._run_batch, args=(urls, out_dir, custom_name), daemon=True).start()

    def _run_batch(self, urls, out_dir, custom_name=None):
        dtype = self.type_var.get()
        total = len(urls)
        request_id = start_request(dtype, "queue", urls, custom_name=custom_name, out_dir=out_dir)
        delay_s = max(0, self.cfg.get("batch_delay_seconds", 0))
        # self.downloader is a shared app-level reference that's never
        # explicitly cleared once a run ends (cancelled or otherwise) -
        # without resetting it here, a RETRY right after a cancelled
        # batch would see the previous downloader's _cancel=True still
        # set on the very first loop check below, break immediately, and
        # incorrectly report the batch as "finished" without downloading
        # anything. This was a real, confirmed bug, not a hypothetical.
        self.downloader = None
        # Reset queue-wide ETA tracking for this run - see
        # _update_eta_label(): the whole-queue estimate is the average
        # of items completed so far in THIS run, not a stale value left
        # over from a previous batch.
        self._batch_item_durations = []
        self._batch_items_remaining = total
        for i, url in enumerate(urls, start=1):
            if self.downloader and self.downloader._cancel:
                break
            update_item(request_id, url, status="downloading")
            self._threadsafe_log(f"--- Queue item {i}/{total} ---")
            self.after(0, lambda i=i, total=total: self.queue_progress_label.configure(text=f"Queue item {i}/{total}"))
            self.downloader = Downloader(progress_callback=self._threadsafe_progress, log_callback=self._threadsafe_log,
                                          ping_ms_provider=lambda: self._network_ping)
            history_entry_id = add_entry(url, url, dtype, "", "Analyzing")
            self.after(0, self._refresh_history_tab)
            media_info = None
            try:
                media_info = fetch_media_info(url)
                name = sanitize_filename(beautify_title(media_info.get("title", f"download_{i}")))
            except Exception as e:
                self._threadsafe_log(f"Could not fetch info for {url}: {e}")
                name = f"download_{i}"

            ext = self.cfg["video_format"] if dtype == "Video" else self.cfg["audio_format"]
            unique_name = make_unique_name(out_dir, name, ext)
            update_item(request_id, url, name=unique_name)
            update_entry(history_entry_id, name=unique_name)
            self.after(0, self._refresh_history_tab)

            if self.cfg.get("duplicate_detection_enabled", True):
                existing = find_previous_download(url, out_dir)
                if existing:
                    self._threadsafe_log(f"Skipped item {i}/{total} - already downloaded to "
                                          f"{existing['path']}.", color="blue")
                    update_item(request_id, url, status="skipped", path=existing["path"])
                    update_entry(history_entry_id, path=existing["path"], status="Skipped (duplicate)")
                    self.after(0, self._refresh_history_tab)
                    self._batch_items_remaining = max(0, self._batch_items_remaining - 1)
                    if delay_s and i < total:
                        time.sleep(min(delay_s, 2))  # a short courtesy pause even on a skip, nothing more
                    continue

            status, path = "Success", ""
            update_entry(history_entry_id, status="In Progress")
            self.after(0, self._refresh_history_tab)
            try:
                if dtype == "Video":
                    self._threadsafe_log(f'Downloading "{unique_name}"', color="blue")
                    path = download_with_retry(
                        self.downloader.download_video, log_callback=self._threadsafe_log,
                        url=url, name=unique_name, out_dir=out_dir,
                        quality_key=self.cfg["video_quality"], fmt=self.cfg["video_format"],
                        playlist=False, subtitles=self.subtitles_var.get(), aspect_ratio=self.aspect_var.get(),
                        cookies_from_browser=self.cfg.get("cookies_from_browser", "none"),
                        prefetched_info=media_info
                    )
                else:
                    self._threadsafe_log(f'Downloading "{unique_name}"', color="blue")
                    path = download_with_retry(
                        self.downloader.download_audio, log_callback=self._threadsafe_log,
                        url=url, name=unique_name, out_dir=out_dir,
                        quality=self.cfg["audio_quality"], fmt=self.cfg["audio_format"],
                        playlist=False, embed_thumbnail=self.cfg.get("embed_thumbnail", True),
                        cookies_from_browser=self.cfg.get("cookies_from_browser", "none")
                    )
                self._threadsafe_log(f"Saved: {path}")
                update_item(request_id, url, status="success", path=path,
                            elapsed_seconds=self.downloader.elapsed_seconds())
            except DownloadCancelled:
                self._threadsafe_log("Batch cancelled.")
                status = "Cancelled"
                removed, still_locked = cleanup_partial_files(out_dir, unique_name)
                if removed:
                    self._threadsafe_log(f"Removed {len(removed)} partial file(s) from the cancelled item.")
                if still_locked:
                    self._threadsafe_log(f"{len(still_locked)} partial file(s) still in use, couldn't be "
                                          f"removed - you may need to delete them manually.", color="red")
                update_item(request_id, url, status="failed", error="Cancelled by user")
                update_entry(history_entry_id, path=path, status=status)
                self.after(0, self._refresh_history_tab)
                break
            except YouTubeBotDetectedError:
                status = "Failed"
                self._handle_bot_detection(request_id, url)
                update_entry(history_entry_id, path=path, status=status)
                self.after(0, self._refresh_history_tab)
                break  # stop the whole queue, not just this item - see _handle_bot_detection
            except CookieAccessError:
                status = "Failed"
                self._handle_cookie_access_error(request_id, url)
                update_entry(history_entry_id, path=path, status=status)
                self.after(0, self._refresh_history_tab)
                break
            except DownloadStageError as e:
                status = "Failed"
                err = f"Failed during {e.stage} for {url}: {e.original}"
                self._threadsafe_log(err, color="red")
                update_item(request_id, url, status="failed", error=err,
                            elapsed_seconds=self.downloader.elapsed_seconds())
            except Exception as e:
                status = "Failed"
                err = f"Unexpected error for {url}: {e}"
                self._threadsafe_log(err, color="red")
                update_item(request_id, url, status="failed", error=err,
                            elapsed_seconds=self.downloader.elapsed_seconds())
            update_entry(history_entry_id, name=unique_name, path=path, status=status)
            self.after(0, self._refresh_history_tab)
            self._batch_items_remaining = max(0, self._batch_items_remaining - 1)
            if status == "Success":
                self._batch_item_durations.append(self.downloader.elapsed_seconds())

            # Space requests out - helps avoid triggering YouTube's bot
            # detection in the first place, not just react to it after
            # the fact. Skipped after the very last item (nothing left to
            # wait for) and broken into small checks against cancel so a
            # long delay doesn't make the Cancel button feel unresponsive.
            if delay_s and i < total and not (self.downloader and self.downloader._cancel):
                waited = 0.0
                while waited < delay_s:
                    if self.downloader and self.downloader._cancel:
                        break
                    time.sleep(min(0.25, delay_s - waited))
                    waited += 0.25

        self.batch_running = False
        finish_request(request_id)
        self._threadsafe_log("Batch queue finished.", color="green")
        self.after(0, lambda: self._set_downloading_state(False, batch=True))
        self.after(0, self._refresh_history_tab)
        self.after(0, self._refresh_requests_tab)

    # ------------------------------------------------------------------ #
    def _set_downloading_state(self, downloading, batch=False):
        state = "disabled" if downloading else "normal"
        self.download_btn.configure(state=state)
        self.batch_btn.configure(state=state)
        self.cancel_btn.configure(state="normal" if downloading else "disabled")
        if downloading:
            self._start_speed_display_tick()
        else:
            self._stop_speed_display_tick()
            self.progress_bar.set(0)
            self.progress_label.configure(text="Idle")
            self.queue_progress_label.configure(text="")
            self.eta_label.configure(text="")
            self._batch_item_durations = []
            self._batch_items_remaining = 0

    def cancel_download(self):
        if self.downloader:
            self.downloader.cancel()
            self.cancel_btn.configure(state="disabled")

    def _threadsafe_log(self, message, level="simple", color=None):
        self.after(0, lambda: self._log(message, level=level, color=color))

    def _threadsafe_progress(self, pct, speed):
        self.after(0, lambda: self._set_progress(pct, speed))

    # ================================================================== #
    # PLAYLISTS TAB
    # ================================================================== #
    def _playlist_folder(self, name):
        """Every playlist gets a real folder on disk under
        <download_root>/Playlists/<name>, created on demand. This is what
        the output-folder dropdown on the Download tab points at when you
        pick a playlist to download directly into, and what "Open folder"/
        "Open in VLC" (whole playlist) act on."""
        root = self.cfg.get("playlists_path", "")
        if not root:
            return None
        return playlist_path(ensure_playlists_root(root), sanitize_filename(name))

    def _build_media_tab(self, tab):
        """The Media tab: two subtabs - Playlists (unchanged from before,
        just relocated) and Library (new - searches across whatever
        folders are configured in Settings > Media Library, any file
        type, not just what this app downloads)."""
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        media_tabview = ctk.CTkTabview(tab)
        media_tabview.grid(row=0, column=0, sticky="nsew")
        playlists_tab = media_tabview.add("Playlists")
        library_tab = media_tabview.add("Library")
        self._build_playlists_subtab(playlists_tab)
        self._build_library_subtab(library_tab)

    def _build_playlists_subtab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(tab, width=200)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 15))
        new_row = ctk.CTkFrame(left, fg_color="transparent")
        new_row.pack(fill="x", padx=10, pady=(10, 4))
        ctk.CTkButton(new_row, text="+ New Playlist", font=self.font_normal,
                      command=self._new_playlist_clicked).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(new_row, text="\u21bb", width=32, font=self.font_normal, fg_color="gray40",
                      hover_color="gray30", command=self._refresh_playlists_tab).pack(side="left", padx=(6, 0))
        ctk.CTkButton(left, text="Import Folder...", font=self.font_normal, fg_color="gray40",
                      hover_color="gray30", command=self._import_playlist_clicked).pack(
            fill="x", padx=10, pady=(0, 6))
        self.playlist_search_var = ctk.StringVar(value="")
        playlist_search_row = ctk.CTkFrame(left, fg_color="transparent")
        playlist_search_row.pack(fill="x", padx=10, pady=(0, 8))
        playlist_search_entry = ctk.CTkEntry(playlist_search_row, textvariable=self.playlist_search_var,
                                              font=self.font_small, placeholder_text="Search playlists...")
        playlist_search_entry.pack(side="left", fill="x", expand=True)
        self._add_search_clear_button(playlist_search_entry, self.playlist_search_var)
        self.playlist_search_var.trace_add(
            "write", lambda *_: self._debounced_call("_playlist_search_after_id", 300, self._refresh_playlists_tab))

        # Advanced Selecting for bulk-deleting playlists - replaces the
        # per-row "Del" button with a checkbox once enabled.
        from gui.advanced_select import AdvancedSelector, build_selection_toolbar
        self.playlist_selector = AdvancedSelector()
        select_toolbar = ctk.CTkFrame(left, fg_color="transparent")
        select_toolbar.pack(fill="x", padx=10, pady=(0, 6))
        build_selection_toolbar(
            select_toolbar, self.playlist_selector,
            all_ids_getter=lambda: list_playlists(self.cfg.get("playlists_path", "")),
            on_delete=self._delete_selected_playlists,
            font_normal=self.font_small, font_small=self.font_small)
        base_on_change = self.playlist_selector.on_change

        def combined_on_change():
            base_on_change()
            self._refresh_playlists_tab()
        self.playlist_selector.on_change = combined_on_change

        self.playlist_list_frame = ctk.CTkScrollableFrame(left, width=180)
        self.playlist_list_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        right = ctk.CTkFrame(tab)
        right.grid(row=0, column=1, sticky="nsew")
        title_row = ctk.CTkFrame(right, fg_color="transparent")
        title_row.pack(fill="x", padx=15, pady=(15, 5))
        title_row.grid_columnconfigure(0, weight=1)
        self.playlist_title_label = ctk.CTkLabel(title_row, text="Select a playlist", font=self.font_label)
        self.playlist_title_label.grid(row=0, column=0, sticky="w")
        self.playlist_open_folder_btn = ctk.CTkButton(
            title_row, text="Open folder", width=100, font=self.font_normal,
            fg_color="gray40", hover_color="gray30", state="disabled",
            command=self._open_playlist_folder)
        self.playlist_open_folder_btn.grid(row=0, column=1, padx=(10, 0))
        self.playlist_open_vlc_btn = ctk.CTkButton(
            title_row, text="Open in VLC", width=100, font=self.font_normal, state="disabled",
            **VLC_BUTTON_COLORS, command=self._open_playlist_in_vlc)
        self.playlist_open_vlc_btn.grid(row=0, column=2, padx=(10, 0))

        ctk.CTkLabel(right, text="This playlist is just a folder on disk - drag files in or out with your "
                                  "regular file manager too, this list always reflects what's actually there.",
                     font=self.font_small, text_color="gray60", wraplength=480, justify="left").pack(
            anchor="w", padx=15, pady=(0, 6))

        self.playlist_items_frame = ctk.CTkScrollableFrame(right)
        self.playlist_items_frame.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        self.selected_playlist = None
        self._refresh_playlists_tab()

    def _refresh_playlists_tab(self):
        for w in self.playlist_list_frame.winfo_children():
            w.destroy()
        root = self.cfg.get("playlists_path", "")
        playlists = list_playlists(root)  # empty list, never an error, if the folder doesn't exist yet
        query = getattr(self, "playlist_search_var", None)
        query = query.get().strip().lower() if query else ""
        if query:
            playlists = [p for p in playlists if query in p.lower()]
        for name in playlists:
            row = ctk.CTkFrame(self.playlist_list_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            if self.playlist_selector.enabled:
                cb_var = ctk.BooleanVar(value=self.playlist_selector.is_selected(name))
                ctk.CTkCheckBox(row, text="", variable=cb_var, width=18,
                                command=lambda n=name: self.playlist_selector.toggle(n)).pack(side="left", padx=(0, 2))
            # A fixed width + truncated display text (the full name is
            # still always used for the actual command) keeps a long
            # playlist name from ever pushing controls out of view -
            # previously an unbounded-width button would just keep
            # growing with the text. No hover tooltip on truncation
            # (deliberately removed - it was showing on effectively
            # every playlist button for anyone with typically-longer
            # names, which read as an unwanted gray box popping up
            # under the button rather than a helpful hint).
            display_name = name if len(name) <= 20 else name[:18] + "..."
            name_btn = ctk.CTkButton(row, text=display_name, font=self.font_normal, anchor="w", width=140,
                                      command=lambda n=name: self._select_playlist(n))
            name_btn.pack(side="left")
            if not self.playlist_selector.enabled:
                ctk.CTkButton(row, text="Del", width=40, font=self.font_small, fg_color="#a13333",
                              hover_color="#7d2626", command=lambda n=name: self._delete_playlist(n)).pack(
                    side="left", padx=(4, 0))
        # Checked against the REAL (unfiltered) list here, not the
        # possibly search-narrowed `playlists` above - otherwise typing
        # in the search box would incorrectly clear the detail panel for
        # a playlist that still exists, just isn't currently matching
        # the search text.
        all_playlist_names = list_playlists(root)
        if self.selected_playlist and self.selected_playlist in all_playlist_names:
            self._select_playlist(self.selected_playlist)
        elif self.selected_playlist and self.selected_playlist not in all_playlist_names:
            self.selected_playlist = None
            self.playlist_title_label.configure(text="Select a playlist")
            self.playlist_open_folder_btn.configure(state="disabled")
            self.playlist_open_vlc_btn.configure(state="disabled")
            for w in self.playlist_items_frame.winfo_children():
                w.destroy()
        self._refresh_output_playlist_dropdown()

    def _new_playlist_clicked(self):
        NewPlaylistDialog(self, self.font_normal, self.font_label, self._create_playlist)

    def _import_playlist_clicked(self):
        root = self.cfg.get("playlists_path", "")
        if not root:
            messagebox.showwarning("No default folder set",
                                    "Set a default download folder first (Settings tab) before "
                                    "importing a playlist - playlists are stored inside it.")
            return
        source_folder = filedialog.askdirectory(title="Choose a folder to import as a playlist")
        if not source_folder:
            return
        name, copied, msg = import_folder_as_playlist(root, source_folder)
        if not name:
            messagebox.showwarning("Import failed", msg)
            return
        self._log(f"Imported playlist: {msg}", color="green" if copied else None)
        self._refresh_playlists_tab()
        self._select_playlist(name)

    def _create_playlist(self, name):
        root = self.cfg.get("playlists_path", "")
        if not root:
            # Using a plain warning popup here rather than an inline
            # status label - this used to silently update a label that
            # lives on the Download tab, invisible from here on the
            # Playlists tab where "+ New Playlist" actually is, so the
            # user was never actually informed at all.
            messagebox.showwarning("No default folder set",
                                    "Set a default download folder first (Settings tab) before "
                                    "creating a playlist - playlists are stored inside it.")
            return
        created_name, msg = create_playlist(root, name)
        if not created_name:
            messagebox.showwarning("Playlist", msg)
        self._refresh_playlists_tab()

    def _prompt_playlist_delete_destination(self, count_description):
        """The multi-option dialog offered when deleting one or more
        playlists: Archived Content, Videos folder, Music folder, a
        folder the user picks, or a real permanent delete - per how
        this was specifically asked for (not just a plain yes/no).
        Returns ("move", dest_dir) / ("delete", None) / (None, None) if
        the user closed the dialog without choosing anything."""
        result = {"action": None, "dest": None}
        win = ctk.CTkToplevel(self)
        win.title("Delete playlist(s)")
        win.geometry("420x320")
        win.grab_set()

        ctk.CTkLabel(win, text=f"Deleting {count_description}.", font=self.font_label).pack(
            padx=20, pady=(20, 4), anchor="w")
        ctk.CTkLabel(win, text="What should happen to the files inside?", font=self.font_normal,
                     text_color="gray60").pack(padx=20, pady=(0, 16), anchor="w")

        def choose_move(dest):
            result["action"] = "move"
            result["dest"] = dest
            win.destroy()

        def choose_custom_folder():
            folder = filedialog.askdirectory(title="Choose a folder for these files")
            if folder:
                choose_move(folder)

        def choose_delete():
            if messagebox.askyesno("Permanently delete?",
                                    "This will permanently delete the files, not just the playlist. "
                                    "This cannot be undone. Continue?"):
                result["action"] = "delete"
                win.destroy()

        from core.paths import ensure_archived_content_folder
        archive_dir = ensure_archived_content_folder(self.cfg.get("download_root", ""))
        ctk.CTkButton(win, text="Move to Archived Content", font=self.font_normal,
                      command=lambda: choose_move(archive_dir)).pack(fill="x", padx=20, pady=4)
        ctk.CTkButton(win, text="Move to Videos folder", font=self.font_normal, fg_color="gray40",
                      hover_color="gray30",
                      command=lambda: choose_move(self.cfg.get("video_path", ""))).pack(fill="x", padx=20, pady=4)
        ctk.CTkButton(win, text="Move to Music folder", font=self.font_normal, fg_color="gray40",
                      hover_color="gray30",
                      command=lambda: choose_move(self.cfg.get("music_path", ""))).pack(fill="x", padx=20, pady=4)
        ctk.CTkButton(win, text="Choose a folder...", font=self.font_normal, fg_color="gray40",
                      hover_color="gray30", command=choose_custom_folder).pack(fill="x", padx=20, pady=4)
        ctk.CTkButton(win, text="Delete files permanently", font=self.font_normal, fg_color="#a13333",
                      hover_color="#7d2626", command=choose_delete).pack(fill="x", padx=20, pady=(4, 4))
        ctk.CTkButton(win, text="Cancel", font=self.font_small, fg_color="transparent",
                      text_color=("gray20", "gray80"), hover_color=("gray85", "gray20"),
                      command=win.destroy).pack(fill="x", padx=20, pady=(8, 16))

        win.wait_window()
        return result["action"], result["dest"]

    def _delete_playlist(self, name):
        action, dest = self._prompt_playlist_delete_destination(f"'{name}'")
        if action is None:
            return
        delete_playlist(self.cfg.get("playlists_path", ""), name,
                        delete_files=(action == "delete"), dest_dir=dest)
        if self.selected_playlist == name:
            self.selected_playlist = None
        self._refresh_playlists_tab()

    def _delete_selected_playlists(self):
        selected = self.playlist_selector.selected_ids()
        if not selected:
            messagebox.showwarning("Delete", "No playlists selected.")
            return
        action, dest = self._prompt_playlist_delete_destination(
            f"{len(selected)} selected playlist{'s' if len(selected) != 1 else ''}")
        if action is None:
            return
        root = self.cfg.get("playlists_path", "")
        for name in selected:
            delete_playlist(root, name, delete_files=(action == "delete"), dest_dir=dest)
            if self.selected_playlist == name:
                self.selected_playlist = None
        self.playlist_selector.clear()
        self._refresh_playlists_tab()

    def _select_playlist(self, name):
        self.selected_playlist = name
        self.playlist_title_label.configure(text=name)
        self.playlist_open_folder_btn.configure(state="normal")
        self.playlist_open_vlc_btn.configure(state="normal")
        for w in self.playlist_items_frame.winfo_children():
            w.destroy()
        root = self.cfg.get("playlists_path", "")
        files = playlist_contents(root, name)  # reads the folder directly - always in sync by construction
        if not files:
            ctk.CTkLabel(self.playlist_items_frame,
                         text="No files yet. Add a finished download from the Download tab, "
                              "or drop files into this folder directly.",
                         font=self.font_small, text_color="gray60").pack(pady=10)
            return
        for filename in files:
            full_path = os.path.join(playlist_path(root, name), filename)
            size_label = format_file_size(os.path.getsize(full_path)) if os.path.exists(full_path) else ""
            row = ctk.CTkFrame(self.playlist_items_frame)
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=filename, font=self.font_normal, anchor="w").pack(
                side="left", fill="x", expand=True, padx=10, pady=8)
            if size_label:
                ctk.CTkLabel(row, text=size_label, font=self.font_small, text_color="gray60").pack(
                    side="left", padx=(0, 8))
            ctk.CTkButton(row, text="Open", width=55, font=self.font_small, **VLC_BUTTON_COLORS,
                          command=lambda p=full_path: self._open_media_or_warn(p)).pack(side="left", padx=4)
            ctk.CTkButton(row, text="Location", width=70, font=self.font_small, fg_color="gray40",
                          hover_color="gray30",
                          command=lambda p=full_path: self._open_or_warn(os.path.dirname(p))).pack(
                side="left", padx=(0, 4))
            ctk.CTkButton(row, text="Remove", width=70, font=self.font_small, fg_color="gray40",
                          hover_color="gray30",
                          command=lambda f=filename: self._remove_from_playlist(f)).pack(side="left", padx=(0, 8))

    def _open_playlist_folder(self):
        if not self.selected_playlist:
            return
        folder = self._playlist_folder(self.selected_playlist)
        if not folder or not open_folder(folder):
            messagebox.showwarning("Not found", "That playlist's folder couldn't be opened.")

    def _open_playlist_in_vlc(self):
        if not self.selected_playlist:
            return
        folder = self._playlist_folder(self.selected_playlist)
        ok, msg = open_in_vlc(folder)
        if not ok:
            messagebox.showwarning("VLC", msg)

    def _remove_from_playlist(self, filename):
        remove_file_from_playlist(self.cfg.get("playlists_path", ""), self.selected_playlist, filename)
        self._select_playlist(self.selected_playlist)

    def _vlc_or_warn(self, path):
        ok, msg = open_in_vlc(path)
        if not ok:
            messagebox.showwarning("VLC", msg)

    def _open_media_or_warn(self, path):
        """File-type-aware open: video/audio go to VLC (if installed),
        everything else opens with the OS's own default app for it -
        consistent per file type rather than always assuming VLC. A
        genuine permissions problem gets the same elevated-access
        redirect offer as _open_with_permission_redirect, rather than
        just a plain "couldn't open this" warning with no path forward."""
        ok, msg = open_media_smart(path)
        if ok:
            return
        if msg == "permission_denied":
            if messagebox.askyesno("Permission needed",
                                    f"This file needs elevated permissions to open:\n\n{path}\n\n"
                                    f"Would you like to open its folder location so you can grant access "
                                    f"(e.g. right-click > Properties > Security, or Run as Administrator)?"):
                open_folder(os.path.dirname(path))
            return
        messagebox.showwarning("Open file", msg)

    def add_last_download_to_playlist(self):
        if not self.last_downloaded_path:
            self._set_inline_status(self.add_to_playlist_status_label,
                                     "Download something first, then add it to a playlist.", "info")
            return
        root = self.cfg.get("playlists_path", "")
        playlists = list_playlists(root)
        if not playlists:
            self._set_inline_status(self.add_to_playlist_status_label,
                                     "Create a playlist first (Playlists tab).", "info")
            return
        self._pick_playlist_dialog(self.last_downloaded_path)

    def _pick_playlist_dialog(self, filepath):
        root = self.cfg.get("playlists_path", "")
        picker = ctk.CTkToplevel(self)
        picker.title("Add to playlist")
        picker.geometry("300x300")
        picker.grab_set()
        ctk.CTkLabel(picker, text=f"Add:\n{os.path.basename(filepath)}", font=self.font_normal,
                     wraplength=260).pack(padx=15, pady=15)
        scroll = ctk.CTkScrollableFrame(picker)
        scroll.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        for name in list_playlists(root):
            def add(n=name):
                add_file_to_playlist(root, n, filepath)
                self._refresh_playlists_tab()
                picker.destroy()
            ctk.CTkButton(scroll, text=name, font=self.font_normal, command=add).pack(fill="x", pady=3)

    # ================================================================== #
    # LIBRARY SUBTAB (part of the Media tab) - searches across whatever
    # folders are configured in Settings > Media Library, any file type,
    # built for someone managing a lot of different kinds of files, not
    # just this app's own video/audio downloads.
    # ================================================================== #
    def _build_library_subtab(self, tab):
        from core.media_library import CATEGORIES, SORT_MODES as LIBRARY_SORT_MODES
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        filter_row = ctk.CTkFrame(tab, fg_color="transparent")
        filter_row.grid(row=0, column=0, sticky="ew", pady=(10, 6))
        self.library_search_var = ctk.StringVar(value="")
        search_entry = ctk.CTkEntry(filter_row, textvariable=self.library_search_var, font=self.font_normal,
                                     placeholder_text="Search your library...")
        search_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._add_search_clear_button(search_entry, self.library_search_var)
        self.library_search_var.trace_add(
            "write", lambda *_: self._debounced_call("_library_search_after_id", 300, self._refresh_library_tab))

        ctk.CTkLabel(filter_row, text="Sort:", font=self.font_small, text_color="gray60").pack(
            side="left", padx=(0, 6))
        self.library_sort_var = ctk.StringVar(value="Best match")
        ScrollableDropdown(filter_row, LIBRARY_SORT_MODES, self.library_sort_var, font=self.font_small,
                            width=170, command=lambda _v: self._refresh_library_tab()).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(filter_row, text="Type:", font=self.font_small, text_color="gray60").pack(
            side="left", padx=(0, 6))
        self.library_category_var = ctk.StringVar(value="All")
        ScrollableDropdown(filter_row, CATEGORIES, self.library_category_var, font=self.font_small,
                            width=110, command=lambda _v: self._refresh_library_tab()).pack(side="left")
        ctk.CTkButton(filter_row, text="\u21bb", width=32, font=self.font_normal, fg_color="gray40",
                      hover_color="gray30", command=self._refresh_library_tab).pack(side="left", padx=(8, 0))

        limit_row = ctk.CTkFrame(tab, fg_color="transparent")
        limit_row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(limit_row, text="Max results to search for:", font=self.font_small,
                     text_color="gray60").pack(side="left", padx=(0, 8))
        self.library_max_results_var = ctk.IntVar(value=self.cfg.get("media_library_max_results", 200))
        max_results_slider = ctk.CTkSlider(limit_row, from_=10, to=1000, number_of_steps=99, width=self._slider_width(),
                                            variable=self.library_max_results_var,
                                            command=self._on_library_max_results_changed)
        max_results_slider.pack(side="left", padx=(0, 10))
        self.library_max_results_label = ctk.CTkLabel(limit_row, text=str(self.library_max_results_var.get()),
                                                        font=self.font_small, width=45)
        self.library_max_results_label.pack(side="left")

        self.library_results_frame = ctk.CTkScrollableFrame(tab)
        self.library_results_frame.grid(row=2, column=0, sticky="nsew")
        self.library_status_label = ctk.CTkLabel(tab, text="", font=self.font_small, text_color="gray60")
        self.library_status_label.grid(row=3, column=0, sticky="w", pady=(4, 0))
        self._refresh_library_tab()

    def _on_library_max_results_changed(self, value):
        n = int(value)
        self.cfg["media_library_max_results"] = n
        self.library_max_results_label.configure(text=str(n))
        save_config(self.cfg)
        # Debounced - dragging this slider fires this callback
        # continuously (potentially dozens of times a second), and a
        # library scan is real filesystem I/O, not free. Without
        # debouncing, dragging across a wide range triggered a full
        # re-scan for every single intermediate value passed through
        # along the way - exactly the "it renders thousands of
        # intermediate results before getting to what I actually
        # wanted" bug. Cancelling any pending scan and rescheduling on
        # every tick means only the LAST value, after a short pause in
        # dragging, actually triggers a real scan.
        if getattr(self, "_library_refresh_after_id", None) is not None:
            self.after_cancel(self._library_refresh_after_id)
        self._library_refresh_after_id = self.after(300, self._refresh_library_tab)

    def _refresh_library_tab(self):
        from core.media_library import scan_library, sort_results
        for w in self.library_results_frame.winfo_children():
            w.destroy()

        directories = self.cfg.get("media_library_directories", [])
        if not directories:
            ctk.CTkLabel(self.library_results_frame,
                         text="No folders configured yet - add some in Settings > Media Library.",
                         font=self.font_normal, text_color="gray60").pack(pady=20)
            self.library_status_label.configure(text="")
            return

        query = self.library_search_var.get().strip()
        category = self.library_category_var.get()
        max_results = self.cfg.get("media_library_max_results", 200)

        start = time.time()
        results = scan_library(directories, query=query, category_filter=category, max_results=max_results,
                                include_subfolders=self.cfg.get("media_library_include_subfolders", True))
        elapsed = time.time() - start
        results = sort_results(results, self.library_sort_var.get())

        if not results:
            ctk.CTkLabel(self.library_results_frame, text="No files found.", font=self.font_normal,
                         text_color="gray60").pack(pady=20)
            self.library_status_label.configure(text=f"Searched in {elapsed:.2f}s.")
            return

        capped_note = f" (stopped at the {max_results}-result limit - raise it above for more)" \
            if len(results) >= max_results else ""
        self.library_status_label.configure(
            text=f"{len(results)} result(s) in {elapsed:.2f}s{capped_note}")

        for item in results:
            row = ctk.CTkFrame(self.library_results_frame)
            row.pack(fill="x", pady=3)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=item["name"], font=self.font_normal, anchor="w").grid(
                row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
            when = datetime.datetime.fromtimestamp(item["modified"]).strftime("%Y-%m-%d %H:%M")
            subtitle = f"{item['category']} - {format_file_size(item['size'])} - {when} - {item['path']}"
            if "duplicate_group" in item:
                subtitle = f"Duplicate group {item['duplicate_group']} - " + subtitle
            ctk.CTkLabel(row, text=subtitle, font=self.font_small, text_color="gray60", anchor="w").grid(
                row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
            ctk.CTkButton(row, text="Open", width=60, font=self.font_small, **VLC_BUTTON_COLORS,
                          command=lambda p=item["path"]: self._open_media_or_warn(p)).grid(
                row=0, column=1, rowspan=2, padx=6)
            ctk.CTkButton(row, text="Location", width=75, font=self.font_small, fg_color="gray40",
                          hover_color="gray30",
                          command=lambda p=item["path"]: self._open_or_warn(os.path.dirname(p))).grid(
                row=0, column=2, rowspan=2, padx=(0, 6))
            ctk.CTkButton(row, text="Archive", width=70, font=self.font_small, fg_color="gray40",
                          hover_color="gray30",
                          command=lambda p=item["path"], n=item["name"]: self._archive_library_file(p, n)).grid(
                row=0, column=3, rowspan=2, padx=(0, 6))
            delete_btn = ctk.CTkButton(row, text="Delete", width=65, font=self.font_small, fg_color="#a13333",
                                        hover_color="#7d2626")
            delete_btn.configure(command=lambda p=item["path"], n=item["name"], b=delete_btn:
                                  self._delete_library_file_clicked(p, n, b))
            delete_btn.grid(row=0, column=4, rowspan=2, padx=(0, 10))

    def _archive_library_file(self, path, name):
        """Moves this file - and any other file sharing the same base
        name in the same folder (e.g. a .jpg thumbnail or .srt subtitle
        that came with it) - to the Archived Content folder under the
        default download folder, per how this was specifically asked
        for ("the file and any other files inside it" - read as
        companion files belonging to the same media item, since a
        single media file doesn't itself "contain" other files)."""
        from core.paths import ensure_archived_content_folder
        from core.playlists import _unique_dest
        archive_dir = ensure_archived_content_folder(self.cfg.get("download_root", ""))
        folder = os.path.dirname(path)
        base_name = os.path.splitext(os.path.basename(path))[0]
        moved = []
        try:
            for filename in os.listdir(folder):
                if os.path.splitext(filename)[0] == base_name:
                    src = os.path.join(folder, filename)
                    dst = _unique_dest(os.path.join(archive_dir, filename))
                    shutil.move(src, dst)
                    moved.append(filename)
        except OSError as e:
            messagebox.showerror("Archive failed", f"Couldn't archive this file:\n{e}")
            return
        self._log(f"Archived {len(moved)} file(s) for '{name}' to {archive_dir}", color="blue")
        self._refresh_library_tab()

    def _is_in_default_download_folder(self, path):
        """Whether `path` lives inside one of the app's own configured
        default folders (download_root, video_path, music_path) - the
        inline blue "Confirm?" delete pattern only applies here; a file
        anywhere else still gets the full popup confirmation, per how
        this was specifically asked for."""
        default_folders = [self.cfg.get("download_root"), self.cfg.get("video_path"), self.cfg.get("music_path")]
        try:
            abs_path = os.path.abspath(path)
            for folder in default_folders:
                if folder and abs_path.startswith(os.path.abspath(folder) + os.sep):
                    return True
        except Exception:
            pass
        return False

    def _delete_library_file_clicked(self, path, name, button):
        """For a file in one of the app's own default folders: the first
        click turns the button itself into a blue "Confirm?" (no popup -
        reduces the effort of deleting a single file, per how this was
        asked for), and the second click actually deletes. A file
        anywhere else still goes through the regular popup confirmation
        - this inline shortcut is deliberately only for the app's own
        default locations, not anywhere on the user's disk."""
        if not self._is_in_default_download_folder(path):
            self._delete_library_file(path, name)
            return
        if button.cget("text") != "Confirm?":
            button.configure(text="Confirm?", fg_color=("#3B8ED0", "#1F6AA5"), hover_color="#1a5a8a")
            # Reverts back to a plain "Delete" after a few seconds if
            # never confirmed, so an accidental first click doesn't
            # leave a live "Confirm?" button sitting there indefinitely.
            button.after(4000, lambda: button.configure(text="Delete", fg_color="#a13333", hover_color="#7d2626")
                          if button.winfo_exists() and button.cget("text") == "Confirm?" else None)
            return
        self._delete_library_file(path, name)

    def _delete_library_file(self, path, name):
        """Deletes the actual file at this path - not a playlist-style
        'remove from the list', a real, permanent delete from disk, per
        how this was specifically asked for. Confirmed first (via the
        popup here, or the inline blue Confirm? button for default-
        folder files - see _delete_library_file_clicked), since there's
        no undo for this one."""
        if not self._is_in_default_download_folder(path):
            if not messagebox.askyesno("Delete file", f"Permanently delete '{name}'?\n\n{path}\n\n"
                                                         f"This cannot be undone."):
                return
        try:
            os.remove(path)
            self._log(f"Deleted: {path}", color="red")
            self._refresh_library_tab()
        except OSError as e:
            messagebox.showerror("Delete failed", f"Couldn't delete this file:\n{e}")

    # ================================================================== #
    # HISTORY TAB
    # ================================================================== #
    def _build_history_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        history_tabview = ctk.CTkTabview(tab)
        history_tabview.grid(row=0, column=0, sticky="nsew")
        general_tab = history_tabview.add("General History")
        requests_tab = history_tabview.add("Request History")

        self._build_general_history_subtab(general_tab)
        self._build_request_history_subtab(requests_tab)

    def _build_general_history_subtab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(3, weight=1)
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="Every download attempt, success or failure.", font=self.font_small,
                     text_color="gray60").grid(row=0, column=0, sticky="w")
        ctk.CTkButton(top, text="\u21bb", width=32, font=self.font_normal, fg_color="gray40",
                      hover_color="gray30", command=self._refresh_history_tab).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(top, text="Clear History", width=110, fg_color="#a13333", hover_color="#7d2626",
                      font=self.font_normal, command=self._clear_history_clicked).grid(row=0, column=2, sticky="e")

        # Same search/sort/type-filter pattern as Request History (and
        # what the upcoming Media tab's library search will also use) -
        # kept consistent across every "browse a list of past things"
        # screen in the app rather than each one inventing its own.
        filter_row = ctk.CTkFrame(tab, fg_color="transparent")
        filter_row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.history_search_var = ctk.StringVar(value="")
        search_entry = ctk.CTkEntry(filter_row, textvariable=self.history_search_var, font=self.font_normal,
                                     placeholder_text="Search downloaded names...")
        search_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._add_search_clear_button(search_entry, self.history_search_var)
        self.history_search_var.trace_add(
            "write", lambda *_: self._debounced_call("_history_search_after_id", 300, self._refresh_history_tab))

        ctk.CTkLabel(filter_row, text="Sort:", font=self.font_small, text_color="gray60").pack(
            side="left", padx=(0, 6))
        self.history_sort_var = ctk.StringVar(value="Newest first")
        ScrollableDropdown(filter_row, HISTORY_SORT_MODES, self.history_sort_var, font=self.font_small,
                            width=150, command=lambda _v: self._refresh_history_tab()).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(filter_row, text="Type:", font=self.font_small, text_color="gray60").pack(
            side="left", padx=(0, 6))
        self.history_type_var = ctk.StringVar(value="All")
        ScrollableDropdown(filter_row, HISTORY_TYPE_FILTERS, self.history_type_var, font=self.font_small,
                            width=110, command=lambda _v: self._refresh_history_tab()).pack(side="left")

        # Advanced Selecting - the app's one reusable multi-select
        # mechanism (see gui/advanced_select.py). Replaces the old
        # always-visible per-row Delete button: with selecting off,
        # rows have no delete control at all; turning it on reveals a
        # checkbox per row plus Select All / bulk Delete Selected here.
        select_row = ctk.CTkFrame(tab, fg_color="transparent")
        select_row.grid(row=2, column=0, sticky="w", pady=(0, 8))
        from gui.advanced_select import AdvancedSelector, build_selection_toolbar
        self.history_selector = AdvancedSelector()
        build_selection_toolbar(
            select_row, self.history_selector,
            all_ids_getter=lambda: [e["id"] for e in load_history()],
            on_delete=self._delete_selected_history_entries,
            font_normal=self.font_normal, font_small=self.font_small)
        base_on_change = self.history_selector.on_change

        def combined_on_change():
            base_on_change()
            self._refresh_history_tab()
        self.history_selector.on_change = combined_on_change

        self.history_frame = ctk.CTkScrollableFrame(tab)
        self.history_frame.grid(row=3, column=0, sticky="nsew")
        self._refresh_history_tab()

    def _delete_selected_history_entries(self):
        selected = self.history_selector.selected_ids()
        if not selected:
            messagebox.showwarning("Delete", "No entries selected.")
            return
        if not messagebox.askyesno("Delete history entries",
                                    f"Delete {len(selected)} selected entr{'y' if len(selected) == 1 else 'ies'}? "
                                    f"This only removes the history log, not any downloaded files."):
            return
        for entry_id in selected:
            delete_entry(entry_id)
        self.history_selector.clear()
        self._refresh_history_tab()

    def _build_request_history_subtab(self, tab):
        # Request History used to live on the Extras tab - moved here
        # since it's fundamentally a history view too, and belongs
        # alongside the general download history rather than mixed in
        # with disclaimer text and developer login.
        outer = ctk.CTkFrame(tab, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=10, pady=10)
        self._raw_refresh_requests_tab = build_request_history_section(self, outer)

    def _refresh_requests_tab(self):
        if getattr(self, "_closing", False):
            return
        raw = getattr(self, "_raw_refresh_requests_tab", None)
        if raw:
            self._debounced_call("_requests_refresh_after_id", 250, raw)

    def _refresh_history_tab(self):
        # Worker threads fire this many times per downloaded item (via
        # self.after(0, ...)). Coalesce the bursts so the actual, expensive
        # rebuild - destroy every row, recreate every row - runs at most a
        # few times a second, not dozens.
        self._debounced_call("_history_refresh_after_id", 250, self._do_refresh_history_tab)

    def _do_refresh_history_tab(self):
        if getattr(self, "_closing", False):
            return
        for w in self.history_frame.winfo_children():
            w.destroy()
        all_history = load_history()
        entries = list(all_history)

        query = getattr(self, "history_search_var", None)
        query = query.get().strip() if query else ""
        if query:
            # Weighted multi-field search: name match ranks above a url
            # match, which ranks above a match in type/status/path -
            # never just "contains or not", the field it matched in
            # actually matters for ordering.
            scored = []
            for e in entries:
                score = weighted_match_score(query, e.get("name", ""), e.get("url", ""),
                                              [e.get("type", ""), e.get("status", ""), e.get("path", "")])
                if score:
                    scored.append((score, e))
            scored.sort(key=lambda pair: pair[0], reverse=True)
            entries = [e for _score, e in scored]

        type_choice = getattr(self, "history_type_var", None)
        type_choice = type_choice.get() if type_choice else "All"
        if type_choice != "All":
            entries = [e for e in entries if e.get("type") == type_choice]

        sort_choice = getattr(self, "history_sort_var", None)
        sort_choice = sort_choice.get() if sort_choice else "Newest first"
        # Sorting (not searching) skips a leading special character in
        # the name, so "[Cool] Video" alphabetizes under "C" - search
        # itself never does this (see weighted_match_score), so typing
        # ".com" or ".org" still works to find literal matches.
        # Always applies the explicit sort choice unconditionally (same
        # pattern as Request History), including on top of a search's
        # relevance ordering - if the user explicitly picks a sort mode
        # while searching, that choice should win for the matched
        # results, not be silently ignored.
        if sort_choice == "Alphabetical (A-Z)":
            entries = sorted(entries, key=lambda e: strip_leading_special(e.get("name", "")).lower())
        elif sort_choice == "Alphabetical (Z-A)":
            entries = sorted(entries, key=lambda e: strip_leading_special(e.get("name", "")).lower(), reverse=True)
        elif sort_choice == "Oldest first":
            # A real chronological sort on the stored date string
            # (YYYY-MM-DD HH:MM sorts correctly as plain text) - NOT a
            # naive reversed() of the current list order, which would
            # only be correct if the list were still in load_history()'s
            # original newest-first order. Once a search has already
            # reordered entries by relevance, reversing that gives
            # "worst match first", not oldest-first.
            entries = sorted(entries, key=lambda e: e.get("date", ""))
        elif sort_choice == "Largest file size":
            def _size(e):
                p = e.get("path", "")
                return os.path.getsize(p) if p and os.path.isfile(p) else -1
            entries = sorted(entries, key=_size, reverse=True)
        # "Newest first" is already load_history()'s natural order (or,
        # with a search active, the relevance-ranked order from above -
        # left as-is rather than re-sorted by date, since "best match
        # first" is what a search result list should show)

        if not entries:
            msg = "No downloads yet." if not all_history else "No downloads match your search/filter."
            ctk.CTkLabel(self.history_frame, text=msg, font=self.font_normal,
                         text_color="gray60").pack(pady=20)
            return
        status_colors = {
            "Success": "#2fa84f",          # green
            "Failed": "#c0392b",           # red
            "Cancelled": "#e0a020",        # orange
            "Analyzing": "#3b8ed0",        # blue - matches "In Progress", both mean "actively working on it"
            "In Progress": "#3b8ed0",      # blue
            "Paused": "gray40",            # dark gray
            "Skipped (duplicate)": "gray50",
        }
        for entry in entries:
            row = ctk.CTkFrame(self.history_frame)
            row.pack(fill="x", pady=4)
            col = 0
            if self.history_selector.enabled:
                cb_var = ctk.BooleanVar(value=self.history_selector.is_selected(entry.get("id")))
                ctk.CTkCheckBox(row, text="", variable=cb_var, width=20,
                                command=lambda eid=entry.get("id"): self.history_selector.toggle(eid)).grid(
                    row=0, column=0, rowspan=2, padx=(10, 2), pady=8)
                col = 1
            row.grid_columnconfigure(col, weight=1)
            title = f"[{entry.get('type', '?')}] {entry.get('name', '')}"
            ctk.CTkLabel(row, text=title, font=self.font_normal, anchor="w").grid(
                row=0, column=col, sticky="ew", padx=10, pady=(8, 0))
            status = entry.get("status", "")
            path = entry.get("path", "")
            size_str = ""
            if path and os.path.isfile(path):
                size_str = f" - {format_file_size(os.path.getsize(path))}"
            subtitle = f"{entry.get('date', '')} - {status}{size_str}"
            ctk.CTkLabel(row, text=subtitle, font=self.font_small, anchor="w",
                         text_color=status_colors.get(status, "gray60")).grid(
                row=1, column=col, sticky="ew", padx=10, pady=(0, 8))
            folder = os.path.dirname(path) or ""
            ctk.CTkButton(row, text="Open folder", width=100, font=self.font_small,
                          command=lambda f=folder: self._open_or_warn(f)).grid(row=0, column=col + 1, rowspan=2, padx=6)
            ctk.CTkButton(row, text="VLC", width=55, font=self.font_small, **VLC_BUTTON_COLORS,
                          command=lambda p=path: self._vlc_or_warn(p)).grid(
                row=0, column=col + 2, rowspan=2, padx=(0, 10))

    def _open_or_warn(self, folder):
        if not open_folder(folder):
            messagebox.showwarning("Not found", "That folder no longer exists.")

    def _delete_history_entry(self, entry_id):
        if entry_id is not None:
            delete_entry(entry_id)
        self._refresh_history_tab()

    def _clear_history_clicked(self):
        if messagebox.askyesno("Clear history", "Remove all download history entries?"):
            clear_history()
            self._refresh_history_tab()

    # ================================================================== #
    # SETTINGS TAB
    # ================================================================== #
    def _build_settings_tab(self, tab):
        scroll = ctk.CTkScrollableFrame(tab)
        scroll.pack(fill="both", expand=True)

        # ============================================================ #
        # FILES
        # ============================================================ #
        self._section_header(scroll, "Files")

        self._sub_header(scroll, "Default Download Folder")
        folder_row = ctk.CTkFrame(scroll, fg_color="transparent")
        folder_row.pack(fill="x", padx=5)
        self.download_root_label = ctk.CTkLabel(folder_row, text=self.cfg.get("download_root", "(not set)"),
                                                  font=self.font_normal, anchor="w")
        self.download_root_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(folder_row, text="Change...", width=100, font=self.font_normal,
                      command=self._change_download_root).pack(side="right")
        self.download_root_status_label = ctk.CTkLabel(scroll, text="", font=self.font_small, anchor="w")
        self.download_root_status_label.pack(anchor="w", padx=5, pady=(2, 0))

        lib_header_row = ctk.CTkFrame(scroll, fg_color="transparent")
        lib_header_row.pack(anchor="w", fill="x")
        self._sub_header(lib_header_row, "Media Library Folders", pack_side="left")
        self._add_hint_icon(lib_header_row, "Folders the Media tab's Library subtab is allowed to scan and "
                             "search - not just this app's own downloads, any folders you manage files in. "
                             "Add as many as you need.").pack(side="left", padx=(4, 0), pady=(14, 6))
        self.library_dirs_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self.library_dirs_frame.pack(fill="x", padx=5, pady=(0, 6))
        ctk.CTkButton(scroll, text="+ Add Folder...", font=self.font_normal, width=160,
                      command=self._add_library_directory).pack(anchor="w", padx=5, pady=(0, 8))
        self.library_subfolders_var = ctk.BooleanVar(
            value=self.cfg.get("media_library_include_subfolders", True))
        ctk.CTkSwitch(scroll, text="Include subfolders when scanning (applies to every folder above)",
                      font=self.font_normal, variable=self.library_subfolders_var,
                      command=self._on_library_subfolders_changed).pack(anchor="w", padx=5, pady=(0, 15))
        self._refresh_library_dirs_list()

        # ============================================================ #
        # DOWNLOAD DEFAULTS
        # ============================================================ #
        self._section_header(scroll, "Download Defaults")

        self._sub_header(scroll, "Video Defaults")
        ctk.CTkLabel(scroll, text="Default quality", font=self.font_normal).pack(anchor="w", padx=5)
        self.video_quality_var = ctk.StringVar(value=self.cfg["video_quality"])
        ScrollableDropdown(scroll, VIDEO_QUALITIES, self.video_quality_var, font=self.font_normal,
                            width=300).pack(anchor="w", padx=5, pady=(2, 8))

        ctk.CTkLabel(scroll, text="Default container/format", font=self.font_normal).pack(anchor="w", padx=5)
        self.video_format_var = ctk.StringVar(value=self.cfg["video_format"])
        ScrollableDropdown(scroll, VIDEO_FORMATS, self.video_format_var, font=self.font_normal,
                            width=300).pack(anchor="w", padx=5, pady=(2, 8))

        ctk.CTkLabel(scroll, text="Default aspect ratio", font=self.font_normal).pack(anchor="w", padx=5)
        self.default_aspect_var = ctk.StringVar(value=self.cfg.get("aspect_ratio", "Any"))
        ScrollableDropdown(scroll, ASPECT_RATIO_OPTIONS, self.default_aspect_var, font=self.font_normal,
                            width=300).pack(anchor="w", padx=5, pady=(2, 8))

        self.playlist_default_var = ctk.BooleanVar(value=self.cfg.get("default_playlist", False))
        ctk.CTkSwitch(scroll, text="Download full playlist by default", font=self.font_normal,
                      variable=self.playlist_default_var).pack(anchor="w", padx=5, pady=(2, 4))
        self.subtitles_default_var = ctk.BooleanVar(value=self.cfg.get("default_subtitles", False))
        ctk.CTkSwitch(scroll, text="Download subtitles by default", font=self.font_normal,
                      variable=self.subtitles_default_var).pack(anchor="w", padx=5, pady=(2, 8))

        self._sub_header(scroll, "Audio Defaults")
        ctk.CTkLabel(scroll, text="Default quality (kbps)", font=self.font_normal).pack(anchor="w", padx=5)
        self.audio_quality_var = ctk.StringVar(value=self.cfg["audio_quality"])
        ScrollableDropdown(scroll, AUDIO_QUALITIES, self.audio_quality_var, font=self.font_normal,
                            width=300).pack(anchor="w", padx=5, pady=(2, 8))

        ctk.CTkLabel(scroll, text="Default format", font=self.font_normal).pack(anchor="w", padx=5)
        self.audio_format_var = ctk.StringVar(value=self.cfg["audio_format"])
        ScrollableDropdown(scroll, AUDIO_FORMATS, self.audio_format_var, font=self.font_normal,
                            width=300).pack(anchor="w", padx=5, pady=(2, 8))

        self.embed_thumb_var = ctk.BooleanVar(value=self.cfg.get("embed_thumbnail", True))
        ctk.CTkSwitch(scroll, text="Embed thumbnail & metadata in audio files", font=self.font_normal,
                      variable=self.embed_thumb_var).pack(anchor="w", padx=5, pady=(2, 8))

        # ============================================================ #
        # GENERAL
        # ============================================================ #
        self._section_header(scroll, "General")

        self._sub_header(scroll, "Clipboard")
        self.clipboard_var = ctk.BooleanVar(value=self.cfg.get("clipboard_watch", True))
        ctk.CTkSwitch(scroll, text="Auto-detect video URLs copied to clipboard", font=self.font_normal,
                      variable=self.clipboard_var).pack(anchor="w", padx=5, pady=(2, 8))

        autosave_header_row = ctk.CTkFrame(scroll, fg_color="transparent")
        autosave_header_row.pack(anchor="w", fill="x")
        self._sub_header(autosave_header_row, "Auto Save", pack_side="left")
        self._add_hint_icon(autosave_header_row, "Automatically saves settings and dev notes so nothing "
                             "is lost if the app closes unexpectedly.").pack(side="left", padx=(4, 0), pady=(14, 6))
        self.auto_save_var = ctk.BooleanVar(value=self.cfg.get("auto_save_enabled", True))
        ctk.CTkSwitch(scroll, text="Enable auto-save", font=self.font_normal, variable=self.auto_save_var,
                      command=self._on_autosave_settings_changed).pack(anchor="w", padx=5, pady=(0, 8))
        autosave_row = ctk.CTkFrame(scroll, fg_color="transparent")
        autosave_row.pack(fill="x", padx=5, pady=(0, 8))
        ctk.CTkLabel(autosave_row, text="Interval (seconds, min 1):", font=self.font_normal).pack(
            side="left", padx=(0, 10))
        self.auto_save_interval_var = ctk.IntVar(value=self.cfg.get("auto_save_interval_s", 5))
        autosave_slider = ctk.CTkSlider(autosave_row, from_=1, to=60, number_of_steps=59, width=self._slider_width(),
                                         variable=self.auto_save_interval_var,
                                         command=self._on_autosave_settings_changed)
        autosave_slider.pack(side="left", padx=(0, 10))
        self.auto_save_interval_label = ctk.CTkLabel(autosave_row, text=f"{self.auto_save_interval_var.get()}s",
                                                      font=self.font_small, width=40)
        self.auto_save_interval_label.pack(side="left")

        # ============================================================ #
        # APPEARANCE
        # ============================================================ #
        self._section_header(scroll, "Appearance")

        self._sub_header(scroll, "Theme")
        ctk.CTkLabel(scroll, text="Theme mode", font=self.font_normal).pack(anchor="w", padx=5)
        self.appearance_var = ctk.StringVar(value=self.cfg["appearance_mode"])
        ScrollableDropdown(scroll, APPEARANCE_MODES, self.appearance_var, font=self.font_normal, width=300,
                            command=lambda m: ctk.set_appearance_mode(m)).pack(anchor="w", padx=5, pady=(2, 8))

        ctk.CTkLabel(scroll, text="Color theme (restart to fully apply)", font=self.font_normal).pack(anchor="w", padx=5)
        self.color_theme_var = ctk.StringVar(value=self.cfg["color_theme"])
        ScrollableDropdown(scroll, COLOR_THEMES, self.color_theme_var, font=self.font_normal,
                            width=300, display_map=COLOR_THEME_LABELS).pack(anchor="w", padx=5, pady=(2, 8))

        self._sub_header(scroll, "Font")
        ctk.CTkLabel(scroll, text="Font family", font=self.font_normal).pack(anchor="w", padx=5)
        self.font_family_var = ctk.StringVar(value=self.cfg["font_family"])
        ScrollableDropdown(scroll, FONT_FAMILIES, self.font_family_var, font=self.font_normal,
                            width=300).pack(anchor="w", padx=5, pady=(2, 8))

        ctk.CTkLabel(scroll, text="Font size", font=self.font_normal).pack(anchor="w", padx=5)
        self.font_size_var = ctk.StringVar(value=str(self.cfg["font_size"]))
        ScrollableDropdown(scroll, [str(s) for s in FONT_SIZES], self.font_size_var, font=self.font_normal,
                            width=300).pack(anchor="w", padx=5, pady=(2, 8))

        self.bold_var = ctk.BooleanVar(value=self.cfg.get("bold_text", False))
        ctk.CTkSwitch(scroll, text="Bold text (accessibility)", font=self.font_normal,
                      variable=self.bold_var).pack(anchor="w", padx=5, pady=(2, 8))

        self._sub_header(scroll, "Background Color")
        bg_row = ctk.CTkFrame(scroll, fg_color="transparent")
        bg_row.pack(fill="x", padx=5, pady=(2, 8))
        ctk.CTkLabel(bg_row, text="Background color:", font=self.font_normal).pack(side="left", padx=(0, 10))
        self._bg_color_preview = ctk.CTkFrame(bg_row, width=28, height=28, corner_radius=6,
                                               fg_color=self.cfg.get("background_color") or self._default_bg_color())
        self._bg_color_preview.pack(side="left", padx=(0, 10))
        ctk.CTkButton(bg_row, text="Choose Color...", font=self.font_normal, width=140,
                      command=self._choose_background_color).pack(side="left", padx=(0, 8))
        ctk.CTkButton(bg_row, text="Reset to Default", font=self.font_small, width=120, fg_color="gray40",
                      hover_color="gray30", command=self._reset_background_color).pack(side="left")

        logdisplay_header_row = ctk.CTkFrame(scroll, fg_color="transparent")
        logdisplay_header_row.pack(anchor="w", fill="x")
        self._sub_header(logdisplay_header_row, "Log Display", pack_side="left")
        self._add_hint_icon(logdisplay_header_row, "Log box height - other content on the Download tab "
                             "resizes to fit around it.").pack(side="left", padx=(4, 0), pady=(14, 6))
        self.log_height_var = ctk.IntVar(value=self.cfg.get("log_box_height", 140))
        log_height_row = ctk.CTkFrame(scroll, fg_color="transparent")
        log_height_row.pack(fill="x", padx=5, pady=(0, 8))
        log_height_slider = ctk.CTkSlider(log_height_row, from_=80, to=500, number_of_steps=42, width=self._slider_width(),
                                           variable=self.log_height_var, command=self._on_log_height_slide)
        log_height_slider.pack(side="left", padx=(0, 10))
        self.log_height_label = ctk.CTkLabel(log_height_row, text=f"{self.log_height_var.get()}px",
                                              font=self.font_small, width=50)
        self.log_height_label.pack(side="left")

        launch_header_row = ctk.CTkFrame(scroll, fg_color="transparent")
        launch_header_row.pack(anchor="w", fill="x")
        self._sub_header(launch_header_row, "Launch Size", pack_side="left")
        self._add_hint_icon(launch_header_row, "Only applies when the app is opened, not while it's already "
                             "running. \"Fullscreen\" maximizes the window (not a borderless fullscreen mode); "
                             "\"Remembered\" also keeps the window locked to a visible part of the screen "
                             "even if your monitor setup changes.").pack(side="left", padx=(4, 0), pady=(14, 6))
        self.launch_resolution_var = ctk.StringVar(value=self.cfg.get("launch_resolution", "Remembered"))
        ScrollableDropdown(scroll, self._resolution_preset_choices(), self.launch_resolution_var,
                            font=self.font_normal, width=300,
                            command=self._on_launch_resolution_changed).pack(anchor="w", padx=5, pady=(0, 8))

        # ============================================================ #
        # ADVANCED
        # ============================================================ #
        self._section_header(scroll, "Advanced")

        bot_header_row = ctk.CTkFrame(scroll, fg_color="transparent")
        bot_header_row.pack(anchor="w", fill="x")
        self._sub_header(bot_header_row, "YouTube Bot Detection", pack_side="left")
        self._add_hint_icon(bot_header_row, "YouTube can flag rapid or repeated downloads as bot traffic. "
                             "These two settings are the actual fix - not just a workaround.").pack(
            side="left", padx=(4, 0), pady=(14, 6))
        ctk.CTkLabel(scroll, text="Cookies from browser (most reliable fix):", font=self.font_normal).pack(
            anchor="w", padx=5)
        self.cookies_browser_var = ctk.StringVar(value=self.cfg.get("cookies_from_browser", "none"))
        ScrollableDropdown(scroll, COOKIE_BROWSER_OPTIONS, self.cookies_browser_var, font=self.font_normal,
                            width=300, display_map=COOKIE_BROWSER_LABELS,
                            command=self._on_cookies_browser_changed).pack(anchor="w", padx=5, pady=(2, 8))
        ctk.CTkLabel(scroll, text="Delay between batch/playlist items (seconds):", font=self.font_normal).pack(
            anchor="w", padx=5)
        delay_row = ctk.CTkFrame(scroll, fg_color="transparent")
        delay_row.pack(fill="x", padx=5, pady=(2, 15))
        self.batch_delay_var = ctk.IntVar(value=self.cfg.get("batch_delay_seconds", 3))
        delay_slider = ctk.CTkSlider(delay_row, from_=0, to=30, number_of_steps=30, width=self._slider_width(),
                                      variable=self.batch_delay_var, command=self._on_batch_delay_changed)
        delay_slider.pack(side="left", padx=(0, 10))
        self.batch_delay_label = ctk.CTkLabel(delay_row, text=f"{self.batch_delay_var.get()}s",
                                               font=self.font_small, width=40)
        self.batch_delay_label.pack(side="left")

        timeout_header_row = ctk.CTkFrame(scroll, fg_color="transparent")
        timeout_header_row.pack(anchor="w", fill="x")
        self._sub_header(timeout_header_row, "Playlist Timeout", pack_side="left")
        self._add_hint_icon(timeout_header_row, "If reading a playlist's info takes longer than this, it's "
                             "treated as hung and stopped instead of freezing the download.").pack(
            side="left", padx=(4, 0), pady=(14, 6))
        ctk.CTkLabel(scroll, text="Playlist lookup timeout (seconds):", font=self.font_normal).pack(
            anchor="w", padx=5, pady=(4, 0))
        timeout_row = ctk.CTkFrame(scroll, fg_color="transparent")
        timeout_row.pack(fill="x", padx=5, pady=(0, 15))
        self.playlist_timeout_var = ctk.IntVar(value=self.cfg.get("playlist_fetch_timeout_s", 60))
        timeout_slider = ctk.CTkSlider(timeout_row, from_=10, to=300, number_of_steps=29, width=self._slider_width(),
                                        variable=self.playlist_timeout_var,
                                        command=self._on_playlist_timeout_changed)
        timeout_slider.pack(side="left", padx=(0, 10))
        self.playlist_timeout_label = ctk.CTkLabel(timeout_row, text=f"{self.playlist_timeout_var.get()}s",
                                                    font=self.font_small, width=45)
        self.playlist_timeout_label.pack(side="left")

        self._sub_header(scroll, "Duplicate Detection")
        dup_switch_row = ctk.CTkFrame(scroll, fg_color="transparent")
        dup_switch_row.pack(anchor="w", padx=5, pady=(0, 15))
        self.duplicate_detection_var = ctk.BooleanVar(value=self.cfg.get("duplicate_detection_enabled", True))
        ctk.CTkSwitch(dup_switch_row, text="Skip duplicate downloads", font=self.font_normal,
                      variable=self.duplicate_detection_var,
                      command=self._on_duplicate_detection_changed).pack(side="left")
        self._add_hint_icon(dup_switch_row, "Skips a URL if it's already been successfully downloaded into "
                             "the same output folder before. Pressing a Retry button (Request History) "
                             "always bypasses this, since a retry is a deliberate re-attempt.").pack(
            side="left", padx=(8, 0))

        self._sub_header(scroll, "Background Downloads")
        bg_switch_row = ctk.CTkFrame(scroll, fg_color="transparent")
        bg_switch_row.pack(anchor="w", padx=5, pady=(0, 15))
        self.background_downloads_var = ctk.BooleanVar(value=self.cfg.get("background_downloads_enabled", False))
        ctk.CTkSwitch(bg_switch_row, text="Continue downloads in the background when the app is closed",
                      font=self.font_normal, variable=self.background_downloads_var,
                      command=self._on_background_downloads_changed).pack(side="left")
        self._add_hint_icon(bg_switch_row, "If you close the app while something is still downloading, a "
                             "separate lightweight background process finishes the remaining queue on its own "
                             "- no window, no GUI - and exits once it's done. Off by default since it's a real "
                             "behavior change (a process keeps running after you've closed the app).").pack(
            side="left", padx=(8, 0))

        self._sub_header(scroll, "Batch Queue")
        batch_switch_row = ctk.CTkFrame(scroll, fg_color="transparent")
        batch_switch_row.pack(anchor="w", padx=5, pady=(0, 15))
        self.dynamic_batch_queue_var = ctk.BooleanVar(value=self.cfg.get("dynamic_batch_queue_enabled", False))
        ctk.CTkSwitch(batch_switch_row, text="Use the dynamic URL list for Batch Queue", font=self.font_normal,
                      variable=self.dynamic_batch_queue_var,
                      command=self._on_dynamic_batch_queue_changed).pack(side="left")
        self._add_hint_icon(batch_switch_row, "Replaces the plain batch queue text box with a scrollable "
                             "list of individually-removable URL entries - press the red X next to any URL to "
                             "drop it (no confirmation needed, since it's undoable), Undo or Ctrl+Z to bring "
                             "it back. Removed URLs aren't truly gone until you actually start the "
                             "download.").pack(side="left", padx=(8, 0))

        # ============================================================ #
        # ACCESSIBILITY
        # ============================================================ #
        self._section_header(scroll, "Accessibility")
        scroll_speed_header_row = ctk.CTkFrame(scroll, fg_color="transparent")
        scroll_speed_header_row.pack(anchor="w", fill="x")
        self._sub_header(scroll_speed_header_row, "Scroll Speed", pack_side="left")
        self._add_hint_icon(scroll_speed_header_row, "How quickly the smooth-scroll animation moves - lower "
                             "is faster, higher is slower and more gradual. Applies everywhere in the app "
                             "immediately.").pack(side="left", padx=(4, 0), pady=(14, 6))
        scroll_speed_row = ctk.CTkFrame(scroll, fg_color="transparent")
        scroll_speed_row.pack(fill="x", padx=5, pady=(0, 15))
        self.scroll_speed_var = ctk.IntVar(value=self.cfg.get("scroll_speed_ms", 8))
        scroll_speed_slider = ctk.CTkSlider(scroll_speed_row, from_=2, to=40, number_of_steps=38,
                                             width=self._slider_width(), variable=self.scroll_speed_var,
                                             command=self._on_scroll_speed_changed)
        scroll_speed_slider.pack(side="left", padx=(0, 10))
        self.scroll_speed_label = ctk.CTkLabel(scroll_speed_row, text=f"{self.scroll_speed_var.get()}ms",
                                                font=self.font_small, width=45)
        self.scroll_speed_label.pack(side="left")

        ctk.CTkButton(scroll, text="Save Settings", font=self.font_label, height=40,
                      command=self._save_settings).pack(fill="x", padx=5, pady=(15, 6))
        self.save_settings_status_label = ctk.CTkLabel(scroll, text="", font=self.font_small, anchor="w")
        self.save_settings_status_label.pack(anchor="w", padx=5, pady=(0, 6))

        io_row = ctk.CTkFrame(scroll, fg_color="transparent")
        io_row.pack(fill="x", padx=5, pady=(0, 4))
        ctk.CTkButton(io_row, text="Export Settings...", font=self.font_normal, fg_color="gray40",
                      hover_color="gray30", command=self._export_settings).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(io_row, text="Import Settings...", font=self.font_normal, fg_color="gray40",
                      hover_color="gray30", command=self._import_settings).pack(
            side="left", fill="x", expand=True, padx=(10, 0))
        self.settings_io_status_label = ctk.CTkLabel(scroll, text="", font=self.font_small, anchor="w")
        self.settings_io_status_label.pack(anchor="w", padx=5, pady=(4, 20))

    def _export_settings(self):
        path = filedialog.asksaveasfilename(
            title="Export settings", defaultextension=".json",
            filetypes=[("JSON files", "*.json")], initialfile="media_downloader_settings.json"
        )
        if not path:
            return

        # Confirmed separately before writing, since these are actual
        # filesystem paths on THIS computer (could reveal folder
        # structure or a username if the export file is later shared
        # with someone else) - per how this was specifically asked for.
        path_fields = {
            "Default download folder": self.cfg.get("download_root"),
            "Video folder": self.cfg.get("video_path"),
            "Music folder": self.cfg.get("music_path"),
            "Media Library folders": ", ".join(self.cfg.get("media_library_directories", []) or []),
        }
        listed = "\n".join(f"- {label}: {value}" for label, value in path_fields.items() if value)
        include_paths = messagebox.askyesno(
            "Include folder paths?",
            "This export will include your default download folder and Media Library folder "
            "paths - the actual locations on this computer:\n\n" + (listed or "(none currently set)") +
            "\n\nInclude these in the export?\n\n"
            "Choose No to export everything else with these left blank, for the person "
            "importing to set themselves.")

        export_cfg = dict(self.cfg)
        if not include_paths:
            export_cfg["download_root"] = ""
            export_cfg["video_path"] = ""
            export_cfg["music_path"] = ""
            export_cfg["media_library_directories"] = []

        try:
            from core.config import export_config_dict
            with open(path, "w") as f:
                json.dump(export_config_dict(export_cfg), f, indent=4)
            self._set_inline_status(self.settings_io_status_label, f"Settings exported to {path}", "success")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    def _import_settings(self):
        path = filedialog.askopenfilename(title="Import settings", filetypes=[("JSON files", "*.json")])
        if not path:
            return
        try:
            with open(path, "r") as f:
                imported = json.load(f)
        except Exception as e:
            messagebox.showerror("Import failed", f"Couldn't read that file: {e}")
            return
        try:
            from core.config import merge_imported_config
            merged, report = merge_imported_config(imported)
        except ValueError as e:
            messagebox.showerror("Import failed", str(e))
            return
        self.cfg = merged
        save_config(self.cfg)
        # The report explains exactly what did/didn't carry over (missing
        # settings filled from defaults, unrecognized ones preserved,
        # incompatible ones skipped) - shown in full via a popup rather
        # than the usual brief inline status, since this is genuinely
        # worth reading once, not just a quick confirmation.
        messagebox.showinfo("Settings imported", "\n".join(report) +
                             "\n\nRestart the app for theme/font/window size to fully apply.")
        self._set_inline_status(self.settings_io_status_label, "Settings imported.", "success")

    def _change_download_root(self):
        new_root = filedialog.askdirectory(title="Select new default download folder")
        if not new_root:
            return
        old_video = self.cfg.get("video_path", "")
        old_music = self.cfg.get("music_path", "")
        new_video, new_music = ensure_media_folders(new_root)
        new_playlists = ensure_playlists_folder(new_root)

        existing_files = list_files(old_video) + list_files(old_music)
        if existing_files:
            def do_move(selected):
                video_files = [f for f in selected if f in list_files(old_video)]
                music_files = [f for f in selected if f in list_files(old_music)]
                moved_v, failed_v = move_files(old_video, new_video, video_files)
                moved_m, failed_m = move_files(old_music, new_music, music_files)
                failed = {**failed_v, **failed_m}
                msg = f"Moved {len(moved_v) + len(moved_m)} file(s)."
                if failed:
                    msg += f" {len(failed)} failed to move."
                self._set_inline_status(self.download_root_status_label, msg,
                                        "success" if not failed else "info")
            MoveFilesDialog(self, existing_files, self.font_normal, self.font_label, do_move)

        self.cfg["download_root"] = new_root
        self.cfg["video_path"] = new_video
        self.cfg["music_path"] = new_music
        self.cfg["playlists_path"] = new_playlists
        save_config(self.cfg)
        self.download_root_label.configure(text=new_root)
        self._log(f"Default download folder changed to {new_root}")

    def _default_bg_color(self):
        return "#242424" if self.cfg.get("appearance_mode", "System") != "Light" else "#f2f2f2"

    def _choose_background_color(self):
        from tkinter import colorchooser
        current = self.cfg.get("background_color") or self._default_bg_color()
        chosen = colorchooser.askcolor(color=current, title="Choose background color", parent=self)
        if chosen and chosen[1]:
            hex_color = chosen[1]
            self.cfg["background_color"] = hex_color
            self._bg_color_preview.configure(fg_color=hex_color)
            self.configure(fg_color=hex_color)
            save_config(self.cfg)

    def _reset_background_color(self):
        self.cfg["background_color"] = None
        default = self._default_bg_color()
        self._bg_color_preview.configure(fg_color=default)
        self.configure(fg_color=default)
        save_config(self.cfg)

    def _on_log_height_slide(self, value):
        height = int(value)
        self.log_height_label.configure(text=f"{height}px")
        self.cfg["log_box_height"] = height
        # Applied live, not just at next launch - the whole point of a
        # slider is seeing the effect immediately. Since the Download tab
        # now lives inside a CTkScrollableFrame (see _build_download_tab),
        # just calling .configure(height=...) on the log box isn't always
        # enough on its own to make the visible change appear without
        # switching tabs and back - the scrollable frame's own canvas
        # doesn't always notice a child's height change and recompute its
        # scrollregion/layout until something else forces it to. Explicit
        # update_idletasks() + a manual scrollregion refresh on the
        # scrollable frame's canvas is what actually makes this apply
        # without needing to reload/switch tabs.
        if hasattr(self, "log_box"):
            self.log_box.configure(height=height)
            self.log_box.update_idletasks()
            frame = getattr(self, "_download_tab_frame", None)
            canvas = getattr(frame, "_parent_canvas", None)
            if canvas is not None:
                try:
                    canvas.update_idletasks()
                    canvas.configure(scrollregion=canvas.bbox("all"))
                except Exception:
                    pass  # a live-refresh nicety - never worth breaking the slider over
        save_config(self.cfg)

    def _save_settings(self):
        self.cfg["video_quality"] = self.video_quality_var.get()
        self.cfg["video_format"] = self.video_format_var.get()
        self.cfg["aspect_ratio"] = self.default_aspect_var.get()
        self.cfg["default_playlist"] = self.playlist_default_var.get()
        self.cfg["default_subtitles"] = self.subtitles_default_var.get()
        self.cfg["audio_quality"] = self.audio_quality_var.get()
        self.cfg["audio_format"] = self.audio_format_var.get()
        self.cfg["embed_thumbnail"] = self.embed_thumb_var.get()
        self.cfg["clipboard_watch"] = self.clipboard_var.get()
        self.cfg["appearance_mode"] = self.appearance_var.get()
        self.cfg["color_theme"] = self.color_theme_var.get()
        self.cfg["font_family"] = self.font_family_var.get()
        self.cfg["font_size"] = int(self.font_size_var.get())
        self.cfg["bold_text"] = self.bold_var.get()
        save_config(self.cfg)
        self._build_fonts()
        # Apply the new default toggles to the current Download tab too
        self.aspect_var.set(self.cfg["aspect_ratio"])
        self.playlist_var.set(self.cfg["default_playlist"])
        self.subtitles_var.set(self.cfg["default_subtitles"])
        self._log("Settings saved. Restart the app for font/color theme changes to fully apply.")
        self._set_inline_status(self.save_settings_status_label, "Settings saved.", "success")

    # ================================================================== #
    # VERSION / DEPENDENCIES TAB
    # ================================================================== #
    def _build_version_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)

        from core.app_info import APP_VERSION, APP_RELEASE_DATE, APP_PUBLISHER
        header = ctk.CTkFrame(tab)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ctk.CTkLabel(header, text="Media Downloader", font=self.font_label).pack(anchor="w", padx=15, pady=(10, 2))
        info_line = f"Version {APP_VERSION}  -  Released {APP_RELEASE_DATE}  -  {APP_PUBLISHER}"
        ctk.CTkLabel(header, text=info_line, font=self.font_small, text_color="gray60").pack(
            anchor="w", padx=15, pady=(0, 10))

        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="Dependencies", font=self.font_label).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(top, text="Update All", font=self.font_normal,
                      command=self._update_all_clicked).grid(row=0, column=1, sticky="e")

        self.version_status_frame = ctk.CTkScrollableFrame(tab)
        self.version_status_frame.grid(row=2, column=0, sticky="nsew")
        tab.grid_rowconfigure(2, weight=1)
        self.dependency_update_status_label = ctk.CTkLabel(tab, text="", font=self.font_small, anchor="w")
        self.dependency_update_status_label.grid(row=3, column=0, sticky="w", pady=(6, 0))

        # --- Uninstall - at the very bottom of the Version tab, per how
        # this was specifically asked for (moved here from the More tab). ---
        uninstall_row = ctk.CTkFrame(tab, fg_color="transparent")
        uninstall_row.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ctk.CTkButton(uninstall_row, text="Uninstall Media Downloader", font=self.font_normal,
                      fg_color="#a13333", hover_color="#7d2626",
                      command=self._uninstall_clicked).pack(anchor="w")

        self._refresh_version_tab()

    def _refresh_version_tab(self):
        for w in self.version_status_frame.winfo_children():
            w.destroy()
        self.version_rows = {}
        for item in deps.check_all():
            row = ctk.CTkFrame(self.version_status_frame)
            row.pack(fill="x", pady=4)
            row.grid_columnconfigure(1, weight=1)
            dot_color = "#2fa84f" if item["ok"] else "#c0392b"
            ctk.CTkLabel(row, text="\u25CF", text_color=dot_color, font=self.font_label, width=20).grid(
                row=0, column=0, rowspan=2, padx=(10, 0))
            ctk.CTkLabel(row, text=item["name"], font=self.font_normal, anchor="w").grid(
                row=0, column=1, sticky="ew", padx=10, pady=(8, 0))
            ctk.CTkLabel(row, text=item["detail"], font=self.font_small, anchor="w", text_color="gray60").grid(
                row=1, column=1, sticky="ew", padx=10, pady=(0, 8))
            btn_text = "Update" if item["ok"] else "Install"
            btn = ctk.CTkButton(row, text=btn_text, width=90, font=self.font_normal,
                                command=lambda it=item: self._install_or_update(it))
            btn.grid(row=0, column=2, rowspan=2, padx=10)
            self.version_rows[item["name"]] = btn

        self._refresh_dependency_banner()

    def _refresh_dependency_banner(self):
        results = deps.check_all()
        missing = [r["name"] for r in results if not r["ok"] and r["kind"] in ("python", "ffmpeg")]
        if missing:
            self.dep_banner.configure(text=f"Missing dependencies: {', '.join(missing)} - see the Version tab.")
        else:
            self.dep_banner.configure(text="")

    def _install_or_update(self, item):
        threading.Thread(target=self._run_dependency_action, args=(item,), daemon=True).start()

    def _run_dependency_action(self, item):
        self.after(0, lambda: self._threadsafe_log(f"Updating {item['name']}..."))
        if item["kind"] == "python":
            ok, msg = deps.install_python_package(item["pip_spec"])
        elif item["kind"] == "ffmpeg":
            ok, msg = deps.install_ffmpeg(progress_callback=lambda m: self._threadsafe_log(m))
        elif item["kind"] == "vlc":
            ok, msg = deps.install_vlc(progress_callback=lambda m: self._threadsafe_log(m))
        else:
            ok, msg = False, "Unknown dependency type."
        self._threadsafe_log(msg)
        self.after(0, lambda: self._set_inline_status(
            self.dependency_update_status_label, f"{item['name']}: {msg}", "success" if ok else None))
        if not ok:
            self.after(0, lambda: messagebox.showerror("Update failed", msg))
        self.after(0, self._refresh_version_tab)

    def _update_all_clicked(self):
        if not messagebox.askyesno("Update all", "Check and install/update every dependency now?"):
            return
        threading.Thread(target=self._run_update_all, daemon=True).start()

    def _run_update_all(self):
        results = deps.check_all()
        summary = []
        for item in results:
            self.after(0, lambda n=item['name']: self._threadsafe_log(f"Updating {n}..."))
            if item["kind"] == "python":
                ok, msg = deps.install_python_package(item["pip_spec"])
            elif item["kind"] == "ffmpeg":
                ok, msg = deps.install_ffmpeg(progress_callback=lambda m: self._threadsafe_log(m))
            elif item["kind"] == "vlc":
                ok, msg = deps.install_vlc(progress_callback=lambda m: self._threadsafe_log(m))
            else:
                ok, msg = False, "Unknown dependency."
            summary.append(f"{item['name']}: {'OK' if ok else 'FAILED'} - {msg}")
        full_msg = " | ".join(summary)
        self.after(0, lambda: self._set_inline_status(
            self.dependency_update_status_label, full_msg, "info", clear_after_ms=12000))
        self.after(0, self._refresh_version_tab)

    # ------------------------------------------------------------------ #
    # Recurring background tasks (clipboard watch, heartbeat) all go
    # through this one hardened scheduler instead of each having its own
    # ad-hoc self.after() loop. Any exception inside a tick is caught and
    # logged (both to crash_log.txt and startup_log.txt) but never allowed
    # to kill the loop - one bad tick reschedules and tries again, it
    # doesn't take the whole recurring task down with it.
    # ------------------------------------------------------------------ #
    def _start_recurring(self, interval_ms, func, label):
        def tick():
            if self._closing:
                return  # window is on its way down - don't run the tick or reschedule another one
            try:
                func()
            except Exception as e:
                from core.crash_log import log_error
                from core.startup_log import mark
                mark(f"recurring task '{label}' raised (non-fatal, rescheduling): {e}")
                log_error(f"Recurring task '{label}' raised:\n{e}")
            finally:
                if not self._closing:
                    self.after(interval_ms, tick)
        self.after(interval_ms, tick)

    def _start_clipboard_watch(self):
        self._start_recurring(1000, self._check_clipboard_tick, "clipboard watch")

    def _check_clipboard_tick(self):
        if not self.cfg.get("clipboard_watch", True):
            return
        try:
            content = self.clipboard_get()
        except Exception:
            # Clipboard holds non-text data (an image, files from Explorer) or
            # is empty - Tk raises TclError reading it as a string. Not worth
            # logging every second; just skip this tick.
            return
        if content and content != self._last_clipboard:
            self._last_clipboard = content
            match = URL_PATTERN.search(content)
            if match:
                url = match.group(0)
                if self.tabview.get() == "Download" and self.inner_tabview.get() == "Single Download" \
                        and not self.url_entry.get().strip():
                    self.url_entry.insert(0, url)
                    self._log(f"Detected URL from clipboard: {url}")

    # ------------------------------------------------------------------ #
    # INTERNET STATUS INDICATOR
    # ------------------------------------------------------------------ #
    def _start_network_monitor(self):
        self._network_tier = "none"
        self._network_ping = None
        self._check_network_tick()  # first check immediately, don't wait 15s
        self._start_recurring(15000, self._check_network_tick, "network monitor")

    def _check_network_tick(self):
        threading.Thread(target=self._check_network_thread, daemon=True).start()

    def _check_network_thread(self):
        from core.network_status import check_connection
        tier, ping = check_connection()
        # check_connection() is a real network call and can take a while -
        # self._closing may well have flipped true (window closed) while
        # this thread was blocked on it. Bail out before scheduling
        # anything back onto the GUI: self.after() itself can still
        # "succeed" even after destroy() (the callback gets queued
        # regardless), but by the time it actually runs there may be no
        # Tk root left at all, which is what used to raise "Too early to
        # create image: no default root window" out of
        # _apply_network_status's CTkImage creation - see crash_log.txt.
        if self._closing:
            return
        try:
            self.after(0, lambda: self._apply_network_status(tier, ping))
        except RuntimeError:
            pass  # window was closed while this background check was still running

    def _apply_network_status(self, tier, ping):
        if self._closing:
            return  # queued via self.after(0, ...) before close, ran after - nothing left to update
        from core.network_status import TIER_LABELS, TIER_COLORS, TIER_ICONS
        was_none = self._network_tier == "none"
        self._network_tier = tier
        self._network_ping = ping

        if tier not in self._network_icon_cache:
            try:
                img = Image.open(resource_path(f"assets/network/{TIER_ICONS[tier]}"))
                self._network_icon_cache[tier] = ctk.CTkImage(img, size=(22, 22))
            except Exception:
                self._network_icon_cache[tier] = None
        icon = self._network_icon_cache.get(tier)
        if icon:
            self.network_icon_label.configure(image=icon)

        label = TIER_LABELS[tier]
        if ping is not None:
            label = f"{int(ping)}ms - {label}"
        self.network_status_label.configure(text=label, text_color=TIER_COLORS[tier])

        # Only pop up the "you're offline" dialog on the transition INTO
        # no-internet, not every 15s while it stays that way - that would
        # be relentless, not helpful.
        if tier == "none" and not was_none:
            self._show_no_internet_popup()

    def _show_no_internet_popup(self):
        from core.network_status import TIER_ICONS
        win = ctk.CTkToplevel(self)
        win.title("No Internet Connection")
        popup_w, popup_h = 420, 240
        # Centered over the MAIN window's current position, not a fixed
        # screen coordinate - this is what makes it follow whichever
        # monitor the app is actually on in a multi-monitor setup,
        # rather than always appearing on the primary display.
        self.update_idletasks()
        main_x, main_y = self.winfo_x(), self.winfo_y()
        main_w, main_h = self.winfo_width(), self.winfo_height()
        popup_x = main_x + (main_w - popup_w) // 2
        popup_y = main_y + (main_h - popup_h) // 2
        win.geometry(f"{popup_w}x{popup_h}+{popup_x}+{popup_y}")
        win.grab_set()
        try:
            img = Image.open(resource_path(f"assets/network/{TIER_ICONS['none']}"))
            icon = ctk.CTkImage(img, size=(48, 48))
            ctk.CTkLabel(win, text="", image=icon).pack(pady=(24, 12))
        except Exception:
            pass
        ctk.CTkLabel(win, text="You're not connected to the internet.", font=self.font_label).pack(pady=(0, 8))
        ctk.CTkLabel(win, text="Downloads will still be attempted, but will likely fail until "
                               "your connection is restored.", font=self.font_small, text_color="gray60",
                     wraplength=370, justify="center").pack(padx=20)
        ctk.CTkButton(win, text="OK", font=self.font_normal, width=120, height=36,
                      command=win.destroy).pack(pady=20)

    # ------------------------------------------------------------------ #

    def _start_heartbeat(self):
        """Marks startup_log.txt at whatever interval is configured
        (default 3s, enabled by default), for as long as the app stays
        open - unless a developer disables it or changes the interval
        via the Developer tab. Kept as its own dedicated self.after()
        loop rather than going through the generic _start_recurring,
        since it needs to be stoppable/restartable at a new interval
        live, which that generic scheduler doesn't support."""
        self._heartbeat_after_id = None
        if self.cfg.get("heartbeat_enabled", True):
            self._heartbeat_tick()

    def _heartbeat_tick(self):
        from core.startup_log import mark
        mark("heartbeat - event loop alive", durable=False)
        interval = max(500, int(self.cfg.get("heartbeat_interval_ms", 3000)))
        self._heartbeat_after_id = self.after(interval, self._heartbeat_tick)

    def _stop_heartbeat(self):
        if getattr(self, "_heartbeat_after_id", None) is not None:
            try:
                self.after_cancel(self._heartbeat_after_id)
            except Exception:
                pass
            self._heartbeat_after_id = None

    def _restart_heartbeat(self):
        """Called from the Developer tab whenever the enabled toggle or
        interval changes, so the change takes effect immediately."""
        self._stop_heartbeat()
        if self.cfg.get("heartbeat_enabled", True):
            self._heartbeat_tick()

    # ------------------------------------------------------------------ #
    # AUTO-SAVE: settings + dev notes, on by default, interval
    # configurable in Settings (minimum 1s). Kept as its own dedicated
    # self.after() loop for the same reason the heartbeat is - needs to
    # be stoppable/restartable at a new interval live.
    # ------------------------------------------------------------------ #
    def _start_autosave(self):
        self._autosave_after_id = None
        if self.cfg.get("auto_save_enabled", True):
            self._autosave_tick()

    def _autosave_tick(self):
        try:
            # Only write config.json when something actually changed since the
            # last write - this tick fires every few seconds for the whole
            # session and re-serializing an unchanged dict to disk is pure
            # churn. Direct save_config() calls elsewhere (settings edits, the
            # close handler) are unaffected.
            cfg_snapshot = json.dumps(self.cfg, sort_keys=True, default=str)
            if cfg_snapshot != getattr(self, "_last_saved_cfg_snapshot", None):
                save_config(self.cfg)
                self._last_saved_cfg_snapshot = cfg_snapshot
            if getattr(self, "_dev_tab_built", False) and hasattr(self, "dev_logs_box"):
                self._save_dev_logs_silent()
            self._save_draft_fields()
            self._update_save_status()
        except Exception as e:
            from core.crash_log import log_error
            log_error(f"Auto-save tick raised (non-fatal):\n{e}")
        interval_ms = max(1000, int(self.cfg.get("auto_save_interval_s", 5) * 1000))
        self._autosave_after_id = self.after(interval_ms, self._autosave_tick)

    def _mark_save_dirty(self, *_args):
        """Called the INSTANT a tracked field actually changes - flips
        the save-status label to "Not saved" right away (not waiting for
        a timer), and resets the "how many clean auto-save cycles in a
        row" counter back to 0, so it takes 3 full cycles with nothing
        further changing before it's considered caught up again."""
        self._save_status_clean_ticks = 0
        if hasattr(self, "save_status_label"):
            self.save_status_label.configure(text="Not saved", text_color="#e0a020")

    def _update_save_status(self):
        """Called once per auto-save tick, after the actual save already
        happened - only flips the label back to "Up to date" after 3
        consecutive ticks with nothing new marked dirty in between, per
        how this was specifically asked for (not immediately after the
        very next save, which would flicker if the user is still
        actively typing)."""
        if not hasattr(self, "save_status_label"):
            return
        self._save_status_clean_ticks += 1
        if self._save_status_clean_ticks >= 3:
            self.save_status_label.configure(text="Up to date", text_color="gray60")

    def _save_draft_fields(self):
        """Part of the regular auto-save cycle - captures the Download
        tab's in-progress text field contents (URL, name, batch queue,
        queue name, output folder) so closing the app mid-thought and
        reopening it picks back up where things were left, as long as
        it's within the 1-hour freshness window (see core/draft_state.py)."""
        from core.draft_state import save_draft_state
        fields = {}
        if hasattr(self, "url_entry"):
            fields["url"] = self.url_entry.get()
        if hasattr(self, "name_entry"):
            fields["name"] = self.name_entry.get()
        if hasattr(self, "output_entry"):
            fields["output"] = self.output_entry.get()
        if hasattr(self, "batch_box"):
            fields["batch"] = self.batch_box.get("1.0", "end-1c")
        if hasattr(self, "queue_name_entry"):
            fields["queue_name"] = self.queue_name_entry.get()
        # Only actually write anything if there's something worth
        # restoring - an all-empty draft isn't worth persisting or
        # later "restoring" as a no-op.
        if any(v.strip() for v in fields.values() if v):
            save_draft_state(fields)

    def _restore_draft_fields(self):
        """Called once at startup - restores whatever was in the Download
        tab's text fields when the app was last closed, as long as it's
        still within the 1-hour freshness window. Older drafts are
        already discarded by load_draft_state() itself, so nothing extra
        is needed here to enforce that."""
        from core.draft_state import load_draft_state
        fields = load_draft_state()
        if not fields:
            return
        if fields.get("url") and hasattr(self, "url_entry"):
            self.url_entry.insert(0, fields["url"])
        if fields.get("name") and hasattr(self, "name_entry"):
            self.name_entry.insert(0, fields["name"])
        if fields.get("output") and hasattr(self, "output_entry"):
            self.output_entry.insert(0, fields["output"])
        if fields.get("batch") and hasattr(self, "batch_box"):
            self.batch_box.insert("1.0", fields["batch"])
        if fields.get("queue_name") and hasattr(self, "queue_name_entry"):
            self.queue_name_entry.insert(0, fields["queue_name"])
        self._log("Restored your unfinished Download tab entries from before the app was last closed.")

    def _stop_autosave(self):
        if getattr(self, "_autosave_after_id", None) is not None:
            try:
                self.after_cancel(self._autosave_after_id)
            except Exception:
                pass
            self._autosave_after_id = None

    def _restart_autosave(self):
        self._stop_autosave()
        if self.cfg.get("auto_save_enabled", True):
            self._autosave_tick()

    def _save_dev_logs_silent(self):
        """Same as _save_dev_logs but without the inline 'saved' message -
        that's appropriate for an explicit click, not for a background
        tick that happens every few seconds regardless of whether
        anything actually changed."""
        path = self.cfg.get("dev_notes_path") or self._default_dev_notes_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.dev_logs_box.get("1.0", "end-1c"))
        except Exception:
            pass  # auto-save failures are logged via the caller's try/except, not surfaced to the user

    def _on_close_requested(self):
        """Distinguishes a normal user-initiated close (X button / Alt+F4)
        from the app disappearing unexpectedly - so future logs make clear
        which one happened. Also remembers the current window size AND
        position (which monitor, and where on it) so the next launch
        reopens in the same place - first-ever launch, with nothing saved
        yet, centers instead (see _apply_launch_geometry)."""
        from core.startup_log import mark
        self._closing = True  # see the note on this flag in __init__
        try:
            self.cfg["window_width"] = self.winfo_width()
            self.cfg["window_height"] = self.winfo_height()
            self.cfg["window_x"] = self.winfo_x()
            self.cfg["window_y"] = self.winfo_y()
            save_config(self.cfg)
        except Exception:
            pass  # never let saving the size/position block the app from closing
        self._maybe_spawn_background_daemon()
        mark("WM_DELETE_WINDOW: user closed the window normally")
        self.destroy()

    def _recover_interrupted_downloads(self):
        """Anything still marked 'downloading' when the app starts was
        interrupted by a previous close or crash - nothing is actually
        downloading it now. Mark those failed (retryable from the Requests
        tab) rather than leaving them frozen forever. The background
        daemon has its own separate recovery that resumes them instead -
        see core/queue_daemon.py's process_pending_queue."""
        try:
            from core.download_requests import reset_stalled_downloads
            n = reset_stalled_downloads(
                "failed", error="Interrupted - the app was closed during this download")
            if n:
                self._log(f"{n} download(s) interrupted by a previous close were marked failed - "
                          f"you can retry them from the Requests tab in the More section.")
                self._refresh_requests_tab()
        except Exception as e:
            from core.crash_log import log_error
            log_error(f"Interrupted-download recovery failed (non-fatal):\n{e}")

    def _maybe_spawn_background_daemon(self):
        """If Settings > Advanced > "Continue downloads in the
        background" is on AND there's still genuinely pending queue
        work, hands off to a detached background process (see
        core/queue_daemon.py / main.py's --daemon flag) before this
        window closes, rather than just abandoning whatever was mid-
        download. A no-op in every other case - most closes shouldn't
        spawn anything."""
        if not self.cfg.get("background_downloads_enabled", False):
            return
        try:
            in_progress, _completed = get_all_requests()
            # "downloading" counts too - the daemon's reset_stalled_downloads()
            # turns those back into resumable "pending" work on its next run.
            has_pending = any(
                item.get("status") in ("pending", "downloading")
                for req in in_progress for item in req["items"].values()
            )
            if not has_pending:
                return
            exe = sys.executable
            args = [exe, "--daemon"] if getattr(sys, "frozen", False) \
                else [exe, os.path.join(install_dir(), "main.py"), "--daemon"]
            creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            subprocess.Popen(args, creationflags=creationflags,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              stdin=subprocess.DEVNULL, close_fds=True)
        except Exception:
            pass  # never let a failed daemon spawn block the app from closing normally


    # ================================================================== #
    # EXTRAS TAB - disclaimer, request history (all users), and the
    # developer login that dynamically opens the Developer tab.
    # ================================================================== #
    def _build_more_tab(self, tab):
        """More has two subtabs: Information (everything that used to
        just be "the More tab" - disclaimer, dev login, uninstall - now
        the default landing subtab) and URL Scraping (new)."""
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        self.more_tabview = ctk.CTkTabview(tab)
        self.more_tabview.grid(row=0, column=0, sticky="nsew")
        info_tab = self.more_tabview.add("Information")
        scraping_tab = self.more_tabview.add("URL Scraping")
        self._build_more_information_subtab(info_tab)
        self._build_url_scraping_subtab(scraping_tab)
        self.more_tabview.set("Information")  # explicit default, even though .add() order already implies it

    def _build_url_scraping_subtab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(tab, text="Scrapes a page (using a real headless browser, so JS-loaded/streamed "
                               "media is caught too, not just what's in the raw HTML) for video/audio URLs, "
                               "then fetches each one's real title before showing you anything.",
                     font=self.font_small, text_color="gray60", wraplength=600, justify="left").grid(
            row=0, column=0, sticky="ew", padx=10, pady=(10, 6))

        input_row = ctk.CTkFrame(tab, fg_color="transparent")
        input_row.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        input_row.grid_columnconfigure(0, weight=1)
        self.scrape_url_entry = ctk.CTkEntry(input_row, placeholder_text="https://... - page to scrape",
                                              font=self.font_normal)
        self.scrape_url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._add_clear_button(input_row, self.scrape_url_entry).grid(row=0, column=1, padx=(0, 8))
        self.scrape_type_var = ctk.StringVar(value="Both")
        ScrollableDropdown(input_row, ["Both", "Video", "Audio"], self.scrape_type_var,
                            font=self.font_normal, width=110).grid(row=0, column=2, padx=(0, 8))
        self.scrape_button = ctk.CTkButton(input_row, text="Scrape", font=self.font_normal, width=100,
                                            command=self._start_url_scrape)
        self.scrape_button.grid(row=0, column=3)

        self.scrape_status_label = ctk.CTkLabel(tab, text="", font=self.font_small, text_color="gray60")
        self.scrape_status_label.grid(row=2, column=0, sticky="w", padx=10, pady=(0, 6))

        # A real, confirmed bug fixed here: this row used to collide with
        # scrape_status_label at the same grid row (row=2 for both, with
        # this row also carrying an unrelated large top padding) - the
        # combination made this toolbar's actual on-screen position
        # unpredictable, reading as "renders far down where it's
        # supposed to render" (a stray leftover pady, and the row
        # collision itself, not a real "far down" position). Now has its
        # own row, normal padding, matching how every other selection
        # toolbar in the app is placed.
        toolbar_row = ctk.CTkFrame(tab, fg_color="transparent")
        toolbar_row.grid(row=3, column=0, sticky="w", padx=10, pady=(0, 6))
        from gui.advanced_select import AdvancedSelector, build_selection_toolbar
        self._scrape_results = []
        self.scrape_selector = AdvancedSelector()
        build_selection_toolbar(
            toolbar_row, self.scrape_selector,
            all_ids_getter=lambda: [r["url"] for r in self._scrape_results],
            on_download=self._download_selected_scrape_results, on_copy=self._copy_selected_scrape_results,
            font_normal=self.font_normal, font_small=self.font_small)
        base_on_change = self.scrape_selector.on_change

        def combined_on_change():
            base_on_change()
            self._refresh_scrape_results_display()
        self.scrape_selector.on_change = combined_on_change

        self.scrape_results_frame = ctk.CTkScrollableFrame(tab)
        self.scrape_results_frame.grid(row=4, column=0, sticky="nsew", padx=10, pady=(0, 10))

    def _start_url_scrape(self):
        url = self.scrape_url_entry.get().strip()
        if not url:
            messagebox.showwarning("URL Scraping", "Enter a URL first.")
            return
        self.scrape_button.configure(state="disabled", text="Scraping...")
        media_type = {"Both": "both", "Video": "video", "Audio": "audio"}.get(self.scrape_type_var.get(), "both")
        threading.Thread(target=self._run_url_scrape, args=(url, media_type), daemon=True).start()

    def _run_url_scrape(self, url, media_type):
        from core.url_scraper import scrape_media_urls, fetch_titles_for_urls

        def log(msg):
            self.after(0, lambda: self.scrape_status_label.configure(text=msg, text_color="gray60"))

        try:
            urls = scrape_media_urls(url, media_type=media_type, log_callback=log)
            named = fetch_titles_for_urls(urls, log_callback=log) if urls else []
        except Exception as e:
            error_text = str(e)
            # Playwright's own error message for this specific case is
            # long, technical, and gives no indication of what to
            # actually DO about it - detected here specifically so the
            # app can offer the actual fix (install the browser, then
            # automatically retry) instead of just dumping that raw
            # text at the user, per how this was specifically asked for
            # after the reported "executable is not found" bug.
            if "Executable doesn't exist" in error_text or "playwright install" in error_text:
                self.after(0, lambda: self._offer_playwright_install_and_retry(url, media_type))
            else:
                self.after(0, lambda: messagebox.showerror("Scrape failed", error_text))
                self.after(0, lambda: self.scrape_button.configure(state="normal", text="Scrape"))
            return

        self._scrape_results = [{"url": u, "name": n} for u, n in named]
        self.after(0, self._refresh_scrape_results_display)
        self.after(0, lambda: self.scrape_button.configure(state="normal", text="Scrape"))
        msg = f"Found {len(named)} item(s)." if named else "No media URLs found on that page."
        self.after(0, lambda: self.scrape_status_label.configure(
            text=msg, text_color="#2fa84f" if named else "gray60"))

    def _offer_playwright_install_and_retry(self, url, media_type):
        """Reached when scraping fails specifically because Playwright's
        Chromium browser isn't installed yet - offers to install it now
        (reusing the same ensure_playwright_browser_installed() the
        installer itself runs) and automatically retries the original
        scrape once it succeeds, rather than leaving the user to figure
        out what "Executable doesn't exist" means and how to fix it."""
        if not messagebox.askyesno(
                "Browser component needed",
                "URL Scraping needs a browser component that hasn't been installed yet "
                "(this is a one-time download, roughly a couple hundred MB).\n\n"
                "Install it now?"):
            self.scrape_button.configure(state="normal", text="Scrape")
            return
        self.scrape_status_label.configure(text="Installing browser component...", text_color="gray60")
        threading.Thread(target=self._install_playwright_then_retry, args=(url, media_type), daemon=True).start()

    def _install_playwright_then_retry(self, url, media_type):
        from core.url_scraper import ensure_playwright_browser_installed
        ok, message = ensure_playwright_browser_installed()
        if ok:
            self.after(0, lambda: self.scrape_status_label.configure(
                text="Browser installed - retrying...", text_color="#2fa84f"))
            self._run_url_scrape(url, media_type)
        else:
            self.after(0, lambda: messagebox.showerror("Install failed", message))
            self.after(0, lambda: self.scrape_button.configure(state="normal", text="Scrape"))

    def _refresh_scrape_results_display(self):
        for w in self.scrape_results_frame.winfo_children():
            w.destroy()
        if not self._scrape_results:
            ctk.CTkLabel(self.scrape_results_frame, text="No results yet - scrape a page above.",
                         font=self.font_normal, text_color="gray60").pack(pady=20)
            return
        for item in self._scrape_results:
            row = ctk.CTkFrame(self.scrape_results_frame)
            row.pack(fill="x", pady=3)
            col = 0
            if self.scrape_selector.enabled:
                cb_var = ctk.BooleanVar(value=self.scrape_selector.is_selected(item["url"]))
                ctk.CTkCheckBox(row, text="", variable=cb_var, width=20,
                                command=lambda u=item["url"]: self.scrape_selector.toggle(u)).grid(
                    row=0, column=0, rowspan=2, padx=(10, 2), pady=8)
                col = 1
            row.grid_columnconfigure(col, weight=1)
            text_col = ctk.CTkFrame(row, fg_color="transparent")
            text_col.grid(row=0, column=col, rowspan=2, sticky="ew", padx=(6, 10), pady=8)
            ctk.CTkLabel(text_col, text=item["name"], font=self.font_normal, anchor="w").pack(anchor="w")
            ctk.CTkLabel(text_col, text=item["url"], font=self.font_small, text_color="gray60",
                         anchor="w").pack(anchor="w")
            ctk.CTkButton(row, text="Download", width=90, font=self.font_small,
                          command=lambda u=item["url"]: self._download_single_scrape_result(u)).grid(
                row=0, column=col + 1, rowspan=2, padx=4)
            ctk.CTkButton(row, text="Copy", width=70, font=self.font_small, fg_color="gray40",
                          hover_color="gray30",
                          command=lambda u=item["url"]: self._copy_url_to_clipboard(u)).grid(
                row=0, column=col + 2, rowspan=2, padx=(0, 10))

    def _copy_url_to_clipboard(self, url):
        self.clipboard_clear()
        self.clipboard_append(url)
        self.scrape_status_label.configure(text="Copied to clipboard.", text_color="#2fa84f")

    def _download_single_scrape_result(self, url):
        items = [r for r in self._scrape_results if r["url"] == url]
        self._queue_scrape_items_for_download(items)

    def _download_selected_scrape_results(self):
        selected = self.scrape_selector.selected_ids()
        if not selected:
            messagebox.showwarning("URL Scraping", "No URLs selected.")
            return
        items = [r for r in self._scrape_results if r["url"] in selected]
        self._queue_scrape_items_for_download(items)

    def _copy_selected_scrape_results(self):
        selected = self.scrape_selector.selected_ids()
        if not selected:
            messagebox.showwarning("URL Scraping", "No URLs selected.")
            return
        items = [r for r in self._scrape_results if r["url"] in selected]
        self.clipboard_clear()
        self.clipboard_append("\n".join(i["url"] for i in items))
        self.scrape_status_label.configure(text=f"Copied {len(items)} URL(s) to clipboard.", text_color="#2fa84f")

    def _queue_scrape_items_for_download(self, items):
        """Loads the given scraped item(s) into the Download tab, ready
        to go - switching Video/Audio to match what's actually being
        downloaded, and switching to Batch Queue (with URLs newline-
        separated, ready for the batch parser) if more than one item is
        involved, or Single Download with the fields pre-filled if
        there's exactly one - per how this was asked for."""
        if not items:
            return
        from core.url_scraper import _classify
        kinds = [_classify(i["url"]) for i in items]
        self.type_var.set("Audio" if kinds and all(k == "audio" for k in kinds) else "Video")
        self._on_type_change(self.type_var.get())

        self.tabview.set("Download")
        if len(items) > 1:
            self.inner_tabview.set("Batch Queue")
            self.batch_box.delete("1.0", "end")
            self.batch_box.insert("1.0", "\n".join(i["url"] for i in items))
        else:
            self.inner_tabview.set("Single Download")
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, items[0]["url"])
            self.name_entry.delete(0, "end")
            self.name_entry.insert(0, sanitize_filename(beautify_title(items[0]["name"])))
        self.scrape_status_label.configure(text=f"Loaded {len(items)} URL(s) into the Download tab.",
                                            text_color="#2fa84f")

    def _build_more_information_subtab(self, tab):
        outer = ctk.CTkScrollableFrame(tab)
        outer.pack(fill="both", expand=True)

        # --- Disclaimer (moved here from the log) ---
        self._section_header(outer, "Disclaimer")
        # Width is 25% of the current window width (same helper the
        # sliders use); height is genuinely computed FROM the
        # disclaimer's own content at that width (see
        # _estimate_wrapped_text_height) rather than a fixed guessed
        # value - so it's never awkwardly cramped with a scrollbar, nor
        # oversized with a lot of empty space, regardless of how long
        # the actual disclaimer text is.
        disclaimer_width = self._slider_width()
        disclaimer_height = self._estimate_wrapped_text_height(self._disclaimer_text, self.font_small,
                                                                 disclaimer_width)
        disclaimer_box = ctk.CTkTextbox(outer, font=self.font_small, width=disclaimer_width,
                                         height=disclaimer_height, wrap="word")
        disclaimer_box.pack(anchor="w", pady=(0, 15))
        disclaimer_box.insert("1.0", self._disclaimer_text)
        disclaimer_box.configure(state="disabled")

        # --- Developer login - deliberately understated: a small,
        # muted text link rather than a full section with its own
        # header and always-visible fields, since this isn't something
        # regular users should be drawn to or distracted by. Clicking
        # it reveals the actual login fields; nothing about them is
        # visible until then. ---
        dev_link_row = ctk.CTkFrame(outer, fg_color="transparent")
        dev_link_row.pack(anchor="w", pady=(10, 4))
        self._dev_login_area = ctk.CTkFrame(outer, fg_color="transparent")

        def toggle_dev_login():
            if self._dev_login_area.winfo_ismapped():
                self._dev_login_area.pack_forget()
            else:
                self._dev_login_area.pack(anchor="w", pady=(0, 15))

        ctk.CTkButton(dev_link_row, text="Developer", font=self.font_small, text_color="gray50",
                      fg_color="transparent", hover_color=("gray85", "gray20"), width=70, height=20,
                      command=toggle_dev_login).pack(anchor="w")

        dev_user_row = ctk.CTkFrame(self._dev_login_area, fg_color="transparent")
        dev_user_row.pack(anchor="w", pady=(4, 6))
        self.dev_user_entry = ctk.CTkEntry(dev_user_row, placeholder_text="Username",
                                            font=self.font_normal, width=250)
        self.dev_user_entry.pack(side="left")
        self._add_clear_button(dev_user_row, self.dev_user_entry).pack(side="left", padx=(6, 0))

        dev_pass_row = ctk.CTkFrame(self._dev_login_area, fg_color="transparent")
        dev_pass_row.pack(anchor="w", pady=(0, 6))
        self.dev_pass_entry = ctk.CTkEntry(dev_pass_row, placeholder_text="Password", show="*",
                                            font=self.font_normal, width=250)
        self.dev_pass_entry.pack(side="left")
        self._add_clear_button(dev_pass_row, self.dev_pass_entry).pack(side="left", padx=(6, 0))
        self.dev_pass_entry.bind("<Return>", lambda e: self._dev_login_clicked())
        self.dev_login_error = ctk.CTkLabel(self._dev_login_area, text="", font=self.font_small,
                                             text_color="#c0392b")
        self.dev_login_error.pack(anchor="w", pady=(0, 6))
        ctk.CTkButton(self._dev_login_area, text="Open Developer Tab", font=self.font_normal,
                      command=self._dev_login_clicked).pack(anchor="w")

    def _dev_login_clicked(self):
        user = self.dev_user_entry.get().strip()
        pw = self.dev_pass_entry.get()
        if check_dev_credentials(user, pw):
            self._dev_authenticated = True
            self._dev_username = user
            self.dev_login_error.configure(text="")
            # Clear the fields on success - nothing left to declutter/keep
            # visible once we're through, and it's a nicer state to leave
            # the More tab in for next time.
            self.dev_user_entry.delete(0, "end")
            self.dev_pass_entry.delete(0, "end")
            if hasattr(self, "_dev_login_area"):
                self._dev_login_area.pack_forget()  # collapse back to just the discrete link
            self._open_developer_tab()
        else:
            self.dev_login_error.configure(text="Incorrect username or password.")
            self.dev_pass_entry.delete(0, "end")

    def _open_developer_tab(self):
        """Adds the Developer tab to the tabview (if it isn't already
        there) and switches to it. This is what makes the Developer tab
        genuinely absent from the app - not just hidden/greyed out -
        until a real login happens, per how this was asked for. Inserted
        right before Version (not just appended at the end) so the
        sidebar order matches Download, Playlists, History, Settings,
        More, Developer, Version even though Developer only ever shows
        up well after the other tabs already exist."""
        if not self._dev_tab_built:
            version_index = self.tabview._order.index("Version") if "Version" in self.tabview._order else None
            dev_tab = self.tabview.add("Developer", index=version_index)
            self._build_developer_tab(dev_tab)
            self._dev_tab_built = True
            self._apply_tab_icon("Developer")
        else:
            self._build_developer_locked_area()  # refresh in case of logout/login since it was built
        self.tabview.set("Developer")

    def _dev_logout_clicked(self):
        self._dev_authenticated = False
        self._dev_username = None
        if self._dev_tab_built:
            self.tabview.delete("Developer")
            self._dev_tab_built = False
        self.tabview.set("Extras")

    # ================================================================== #
    # DEVELOPER TAB - only exists after a successful login from Extras.
    # ================================================================== #
    def _build_developer_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        outer = ctk.CTkScrollableFrame(tab)
        outer.grid(row=0, column=0, sticky="nsew")
        tab.grid_rowconfigure(0, weight=1)
        self.dev_tab_outer = outer
        self._build_developer_locked_area()

    def _build_developer_locked_area(self):
        outer = self.dev_tab_outer
        for w in outer.winfo_children():
            w.destroy()

        ctk.CTkLabel(outer, text=f"Developer Tools - logged in as {self._dev_username}",
                     font=self.font_label).pack(anchor="w", pady=(0, 4))
        ctk.CTkButton(outer, text="Log out", font=self.font_small, width=100, fg_color="gray40",
                      hover_color="gray30", command=self._dev_logout_clicked).pack(anchor="w", pady=(0, 15))

        # --- File access ---
        self._section_header(outer, "App Files")
        file_row = ctk.CTkFrame(outer, fg_color="transparent")
        file_row.pack(fill="x", pady=(0, 15))
        ctk.CTkButton(file_row, text="Options File", font=self.font_normal, width=160,
                      command=self._open_options_file).pack(side="left", padx=(0, 8), pady=4)
        ctk.CTkButton(file_row, text="Error Log", font=self.font_normal, width=160,
                      command=self._open_error_log_file).pack(side="left", padx=(0, 8), pady=4)
        ctk.CTkButton(file_row, text="Update Helper", font=self.font_normal, width=160,
                      command=self._open_update_helper_file).pack(side="left", padx=(0, 8), pady=4)
        ctk.CTkButton(file_row, text="Startup Log", font=self.font_normal, width=160,
                      command=self._open_startup_log_file).pack(side="left", padx=(0, 8), pady=4)
        file_row2 = ctk.CTkFrame(outer, fg_color="transparent")
        file_row2.pack(fill="x", pady=(0, 15))
        ctk.CTkButton(file_row2, text="History File", font=self.font_normal, width=160,
                      command=self._open_history_file).pack(side="left", padx=(0, 8), pady=4)
        ctk.CTkButton(file_row2, text="Requests File", font=self.font_normal, width=160,
                      command=self._open_requests_file).pack(side="left", padx=(0, 8), pady=4)
        ctk.CTkButton(file_row2, text="Playlists Folder", font=self.font_normal, width=160,
                      command=self._open_playlists_file).pack(side="left", padx=(0, 8), pady=4)
        ctk.CTkButton(file_row2, text="Open App Data Folder", font=self.font_normal, width=180,
                      fg_color="gray40", hover_color="gray30",
                      command=lambda: open_folder(app_dir())).pack(side="left", padx=(0, 8), pady=4)

        # --- Developer feature toggles ---
        self._section_header(outer, "Developer Features")
        ctk.CTkLabel(outer, text="Anything developer-related in the app is gated behind a switch here.",
                     font=self.font_small, text_color="gray60").pack(anchor="w", pady=(0, 8))

        self.dev_feature_vars = {}
        dev_features = [
            ("dev_log_mode_enabled", "Enable 'Developer' log display mode on the Download tab"),
            ("dev_request_mode_enabled", "Enable 'Developer' display mode for Request History"),
            ("dev_show_raw_ytdlp", "Show raw yt-dlp progress dictionaries in Developer log mode"),
        ]
        for key, label in dev_features:
            var = ctk.BooleanVar(value=self.cfg.get(key, False))
            self.dev_feature_vars[key] = var
            ctk.CTkSwitch(outer, text=label, font=self.font_normal, variable=var,
                          command=self._save_dev_features).pack(anchor="w", pady=3)

        # --- Loading delay - purely visual smoothing on every tab
        # switch (see gui/sidebar_tabview.py's _maybe_show_loading_overlay).
        # Off by default; both whether it's on AND how long it lasts are
        # controlled from here, per how this was specifically asked for. ---
        loading_delay_header_row = ctk.CTkFrame(outer, fg_color="transparent")
        loading_delay_header_row.pack(anchor="w", fill="x")
        loading_delay_header_wrapper = self._section_header(loading_delay_header_row, "Loading Delay")
        self._add_hint_icon(loading_delay_header_wrapper, "Shows a brief neutral overlay on every tab switch - "
                             "purely a visual smoothing effect meant to minimize what the user sees "
                             "mid-transition, not an actual delay to when the tab itself becomes active "
                             "underneath.").pack(side="left", padx=(4, 0), pady=(0, 8))
        self.loading_delay_enabled_var = ctk.BooleanVar(value=self.cfg.get("loading_delay_enabled", False))
        ctk.CTkSwitch(outer, text="Enable loading delay on tab switches", font=self.font_normal,
                      variable=self.loading_delay_enabled_var,
                      command=self._on_loading_delay_enabled_changed).pack(anchor="w", pady=(0, 8))
        loading_delay_row = ctk.CTkFrame(outer, fg_color="transparent")
        loading_delay_row.pack(fill="x", pady=(0, 15))
        ctk.CTkLabel(loading_delay_row, text="Duration:", font=self.font_normal).pack(side="left", padx=(0, 10))
        loading_delay_slider = ctk.CTkSlider(loading_delay_row, from_=100, to=2000, number_of_steps=38,
                                              width=self._slider_width(),
                                              variable=ctk.IntVar(value=self.cfg.get("loading_delay_ms", 500)),
                                              command=self._on_loading_delay_ms_changed)
        loading_delay_slider.pack(side="left", padx=(0, 10))
        self.loading_delay_ms_label = ctk.CTkLabel(loading_delay_row,
                                                    text=f"{self.cfg.get('loading_delay_ms', 500)}ms",
                                                    font=self.font_small, width=50)
        self.loading_delay_ms_label.pack(side="left")

        # --- Heartbeat controls ---
        heartbeat_header_row = ctk.CTkFrame(outer, fg_color="transparent")
        heartbeat_header_row.pack(anchor="w", fill="x")
        heartbeat_header_wrapper = self._section_header(heartbeat_header_row, "Window Heartbeat")
        self._add_hint_icon(heartbeat_header_wrapper, "A recurring log line proving the app's main loop is "
                             "still alive - useful for diagnosing a hang or crash. On by default.").pack(
            side="left", padx=(4, 0), pady=(0, 8))
        self.heartbeat_enabled_var = ctk.BooleanVar(value=self.cfg.get("heartbeat_enabled", True))
        ctk.CTkSwitch(outer, text="Enable heartbeat", font=self.font_normal, variable=self.heartbeat_enabled_var,
                      command=self._on_heartbeat_settings_changed).pack(anchor="w", pady=(0, 8))
        interval_row = ctk.CTkFrame(outer, fg_color="transparent")
        interval_row.pack(fill="x", pady=(0, 15))
        ctk.CTkLabel(interval_row, text="Interval (ms):", font=self.font_normal).pack(side="left", padx=(0, 10))
        self.heartbeat_interval_var = ctk.IntVar(value=self.cfg.get("heartbeat_interval_ms", 3000))
        heartbeat_slider = ctk.CTkSlider(interval_row, from_=500, to=15000, number_of_steps=29, width=self._slider_width(),
                                          variable=self.heartbeat_interval_var,
                                          command=self._on_heartbeat_settings_changed)
        heartbeat_slider.pack(side="left", padx=(0, 10))
        self.heartbeat_interval_label = ctk.CTkLabel(interval_row, text=f"{self.heartbeat_interval_var.get()}ms",
                                                      font=self.font_small, width=60)
        self.heartbeat_interval_label.pack(side="left")



        # --- Dev Notes - can point at ANY text file the developer wants,
        # not just a fixed DEV_LOGS.txt - lets them keep notes wherever
        # they already keep notes, synced/backed-up folders included. ---
        self._section_header(outer, "Dev Notes")
        path_row = ctk.CTkFrame(outer, fg_color="transparent")
        path_row.pack(fill="x", pady=(0, 6))
        current_path = self.cfg.get("dev_notes_path") or self._default_dev_notes_path()
        self.dev_notes_path_label = ctk.CTkLabel(path_row, text=current_path, font=self.font_small,
                                                  text_color="gray60", anchor="w")
        self.dev_notes_path_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(path_row, text="Choose File...", width=120, font=self.font_small,
                      command=self._choose_dev_notes_file).pack(side="left", padx=(8, 0))
        self.dev_logs_box = ctk.CTkTextbox(outer, font=self.font_normal, height=160, wrap="word")
        self.dev_logs_box.pack(fill="x", pady=(0, 0))
        self.dev_logs_box.insert("1.0", self._read_dev_logs())
        self._make_vertically_resizable(self.dev_logs_box)
        ctk.CTkButton(outer, text="Save Dev Notes", font=self.font_normal,
                      command=self._save_dev_logs).pack(anchor="w", pady=(0, 4))
        self.dev_logs_status_label = ctk.CTkLabel(outer, text="", font=self.font_small, anchor="w")
        self.dev_logs_status_label.pack(anchor="w", pady=(0, 15))

        # --- Grant developer access to someone else ---
        grant_header_row = ctk.CTkFrame(outer, fg_color="transparent")
        grant_header_row.pack(anchor="w", fill="x")
        grant_header_wrapper = self._section_header(grant_header_row, "Grant Developer Access")
        self._add_hint_icon(grant_header_wrapper, "Requires the primary developer password. Creates a new "
                             "login (stored only on this machine) for someone else to use.").pack(
            side="left", padx=(4, 0), pady=(0, 8))
        grant_frame = ctk.CTkFrame(outer, fg_color="transparent")
        grant_frame.pack(anchor="w", pady=(0, 6))

        my_pass_wrap = ctk.CTkFrame(grant_frame, fg_color="transparent")
        my_pass_wrap.grid(row=0, column=0, padx=(0, 8), pady=4)
        self.grant_my_pass_entry = ctk.CTkEntry(my_pass_wrap, placeholder_text="Your (primary) password",
                                                 show="*", font=self.font_normal, width=190)
        self.grant_my_pass_entry.pack(side="left")
        self._add_clear_button(my_pass_wrap, self.grant_my_pass_entry).pack(side="left", padx=(6, 0))

        new_user_wrap = ctk.CTkFrame(grant_frame, fg_color="transparent")
        new_user_wrap.grid(row=0, column=1, padx=(0, 8), pady=4)
        self.grant_new_user_entry = ctk.CTkEntry(new_user_wrap, placeholder_text="New username",
                                                  font=self.font_normal, width=190)
        self.grant_new_user_entry.pack(side="left")
        self._add_clear_button(new_user_wrap, self.grant_new_user_entry).pack(side="left", padx=(6, 0))

        new_pass_wrap = ctk.CTkFrame(grant_frame, fg_color="transparent")
        new_pass_wrap.grid(row=1, column=0, padx=(0, 8), pady=4)
        self.grant_new_pass_entry = ctk.CTkEntry(new_pass_wrap, placeholder_text="New password", show="*",
                                                  font=self.font_normal, width=190)
        self.grant_new_pass_entry.pack(side="left")
        self._add_clear_button(new_pass_wrap, self.grant_new_pass_entry).pack(side="left", padx=(6, 0))

        ctk.CTkButton(grant_frame, text="Grant Access", font=self.font_normal,
                      command=self._grant_dev_access_clicked).grid(row=1, column=1, sticky="w", padx=(0, 8), pady=4)
        for entry in (self.grant_my_pass_entry, self.grant_new_user_entry, self.grant_new_pass_entry):
            entry.bind("<Return>", lambda e: self._grant_dev_access_clicked())
        self.grant_result_label = ctk.CTkLabel(outer, text="", font=self.font_small)
        self.grant_result_label.pack(anchor="w", pady=(4, 15))

    def _save_dev_features(self):
        for key, var in self.dev_feature_vars.items():
            self.cfg[key] = var.get()
        save_config(self.cfg)

    def _on_heartbeat_settings_changed(self, _value=None):
        self.cfg["heartbeat_enabled"] = self.heartbeat_enabled_var.get()
        self.cfg["heartbeat_interval_ms"] = int(self.heartbeat_interval_var.get())
        self.heartbeat_interval_label.configure(text=f"{self.cfg['heartbeat_interval_ms']}ms")
        save_config(self.cfg)
        self._restart_heartbeat()

    def _on_autosave_settings_changed(self, _value=None):
        self.cfg["auto_save_enabled"] = self.auto_save_var.get()
        self.cfg["auto_save_interval_s"] = max(1, int(self.auto_save_interval_var.get()))
        self.auto_save_interval_label.configure(text=f"{self.cfg['auto_save_interval_s']}s")
        save_config(self.cfg)
        self._restart_autosave()

    def _on_cookies_browser_changed(self, value):
        self.cfg["cookies_from_browser"] = value
        save_config(self.cfg)

    def _on_batch_delay_changed(self, value):
        seconds = int(value)
        self.cfg["batch_delay_seconds"] = seconds
        self.batch_delay_label.configure(text=f"{seconds}s")
        save_config(self.cfg)

    def _on_playlist_timeout_changed(self, value):
        seconds = int(value)
        self.cfg["playlist_fetch_timeout_s"] = seconds
        self.playlist_timeout_label.configure(text=f"{seconds}s")
        save_config(self.cfg)

    def _on_duplicate_detection_changed(self):
        self.cfg["duplicate_detection_enabled"] = self.duplicate_detection_var.get()
        save_config(self.cfg)

    def _on_background_downloads_changed(self):
        self.cfg["background_downloads_enabled"] = self.background_downloads_var.get()
        save_config(self.cfg)

    def _on_scroll_speed_changed(self, value):
        ms = int(value)
        self.cfg["scroll_speed_ms"] = ms
        self.scroll_speed_label.configure(text=f"{ms}ms")
        from gui.smooth_scroll import set_scroll_speed
        set_scroll_speed(ms)  # applied live, everywhere, immediately - see smooth_scroll.py's module-level design
        save_config(self.cfg)

    def _get_loading_delay_setting(self):
        """The provider SidebarTabview.set() calls on every switch to
        decide whether/how long to show its brief loading overlay - see
        gui/sidebar_tabview.py's _maybe_show_loading_overlay. Reads
        straight from self.cfg each time (not cached), so a change made
        in the Developer tab applies to the very next tab switch."""
        return self.cfg.get("loading_delay_enabled", False), self.cfg.get("loading_delay_ms", 500)

    def _on_loading_delay_enabled_changed(self):
        self.cfg["loading_delay_enabled"] = self.loading_delay_enabled_var.get()
        save_config(self.cfg)

    def _on_loading_delay_ms_changed(self, value):
        ms = int(value)
        self.cfg["loading_delay_ms"] = ms
        self.loading_delay_ms_label.configure(text=f"{ms}ms")
        save_config(self.cfg)

    def _refresh_library_dirs_list(self):
        from core.media_library import discover_subdirectories
        for w in self.library_dirs_frame.winfo_children():
            w.destroy()
        dirs = self.cfg.get("media_library_directories", [])
        if not dirs:
            ctk.CTkLabel(self.library_dirs_frame, text="No folders added yet - the Library subtab will be "
                                                         "empty until you add at least one.",
                         font=self.font_small, text_color="gray60").pack(anchor="w", pady=4)
            return
        for directory in dirs:
            row = ctk.CTkFrame(self.library_dirs_frame, fg_color="transparent")
            row.pack(fill="x", pady=(2, 0))
            exists = os.path.isdir(directory)
            label_color = "gray60" if exists else "#c0392b"
            label_text = directory if exists else f"{directory} (not found)"
            ctk.CTkLabel(row, text=label_text, font=self.font_small, text_color=label_color,
                         anchor="w").pack(side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="Remove", width=70, font=self.font_small, fg_color="#a13333",
                          hover_color="#7d2626",
                          command=lambda d=directory: self._remove_library_directory(d)).pack(side="left")
            # A small preview of what's actually inside this folder -
            # confirms what the "Include subfolders" toggle below would
            # bring into the scan, without needing to go dig through it
            # in a file manager first.
            if exists:
                subdirs = discover_subdirectories(directory)
                if subdirs:
                    preview = ", ".join(os.path.basename(d) for d in subdirs[:8])
                    if len(subdirs) > 8:
                        preview += f", +{len(subdirs) - 8} more"
                    ctk.CTkLabel(self.library_dirs_frame, text=f"    contains: {preview}",
                                 font=self.font_small, text_color="gray50", anchor="w",
                                 wraplength=460, justify="left").pack(anchor="w", pady=(0, 4))

    def _add_library_directory(self):
        chosen = filedialog.askdirectory(title="Add a folder for the Media Library to scan")
        if not chosen:
            return
        dirs = self.cfg.setdefault("media_library_directories", [])
        if chosen in dirs:
            return
        dirs.append(chosen)
        save_config(self.cfg)
        self._refresh_library_dirs_list()

    def _remove_library_directory(self, directory):
        dirs = self.cfg.get("media_library_directories", [])
        if directory in dirs:
            dirs.remove(directory)
            save_config(self.cfg)
        self._refresh_library_dirs_list()

    def _on_library_subfolders_changed(self):
        self.cfg["media_library_include_subfolders"] = self.library_subfolders_var.get()
        save_config(self.cfg)

    def _on_launch_resolution_changed(self, value):
        # Normalize the display label back to the plain "Remembered" the
        # config/launch-geometry code actually checks for.
        stored = "Remembered" if value.startswith("Remembered") else value
        self.cfg["launch_resolution"] = stored
        save_config(self.cfg)

    def _default_dev_notes_path(self):
        return os.path.join(app_dir(), "options", "DEV_LOGS.txt")

    def _choose_dev_notes_file(self):
        path = filedialog.asksaveasfilename(
            title="Choose (or create) a dev notes file", defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile="DEV_LOGS.txt"
        )
        if not path:
            return
        # If the chosen file already has content, load it in rather than
        # clobbering it - picking an EXISTING notes file should show what's
        # already there, not blank it out.
        existing = ""
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing = f.read()
            except Exception:
                pass
        self.cfg["dev_notes_path"] = path
        save_config(self.cfg)
        self.dev_notes_path_label.configure(text=path)
        self.dev_logs_box.delete("1.0", "end")
        self.dev_logs_box.insert("1.0", existing)
        self._set_inline_status(self.dev_logs_status_label, f"Now using {path}", "info")

    def _read_dev_logs(self):
        path = self.cfg.get("dev_notes_path") or self._default_dev_notes_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                return ""
        return ""

    def _save_dev_logs(self):
        path = self.cfg.get("dev_notes_path") or self._default_dev_notes_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.dev_logs_box.get("1.0", "end-1c"))
            self._set_inline_status(self.dev_logs_status_label, "Dev notes saved.", "success")
        except Exception as e:
            show_error("Save failed", str(e), parent=self)

    def _grant_dev_access_clicked(self):
        my_pass = self.grant_my_pass_entry.get()
        new_user = self.grant_new_user_entry.get().strip()
        new_pass = self.grant_new_pass_entry.get()
        ok, msg = grant_dev_access(my_pass, new_user, new_pass)
        self.grant_result_label.configure(text=msg, text_color="#2fa84f" if ok else "#c0392b")
        if ok:
            self.grant_my_pass_entry.delete(0, "end")
            self.grant_new_user_entry.delete(0, "end")
            self.grant_new_pass_entry.delete(0, "end")

    def _open_with_permission_redirect(self, path, not_found_message):
        """The shared handler for anything that opens a file via
        core.utils.open_file/open_media_smart: on success, does nothing
        further; on a genuine permissions problem (detected via the
        "permission_denied" sentinel those functions return), offers to
        open the file's containing folder instead so the user can
        actually do something about it - grant themselves access via
        the folder's own right-click menu (Properties > Security on
        Windows, Get Info on macOS, or the file manager's permissions
        dialog on Linux) rather than just being told "couldn't open
        this" with no path forward. Any other failure (file missing,
        etc) falls back to the plain not-found message callers already
        had."""
        ok, msg = open_file(path)
        if ok:
            return
        if msg == "permission_denied":
            if messagebox.askyesno("Permission needed",
                                    f"This file needs elevated permissions to open:\n\n{path}\n\n"
                                    f"Would you like to open its folder location so you can grant access "
                                    f"(e.g. right-click > Properties > Security, or Run as Administrator)?"):
                open_folder(os.path.dirname(path))
            return
        messagebox.showwarning("Not found", not_found_message)

    def _open_options_file(self):
        from core.config import CONFIG_PATH
        self._open_with_permission_redirect(CONFIG_PATH, f"Options file not found:\n{CONFIG_PATH}")

    def _open_error_log_file(self):
        from core.crash_log import CRASH_LOG_PATH
        self._open_with_permission_redirect(CRASH_LOG_PATH, f"No error log yet at:\n{CRASH_LOG_PATH}")

    def _open_startup_log_file(self):
        from core.startup_log import STARTUP_LOG_PATH
        self._open_with_permission_redirect(STARTUP_LOG_PATH, f"No startup log yet at:\n{STARTUP_LOG_PATH}")

    def _open_history_file(self):
        from core.history import HISTORY_PATH
        self._open_with_permission_redirect(HISTORY_PATH, f"No history file yet at:\n{HISTORY_PATH}")

    def _open_requests_file(self):
        from core.download_requests import REQUESTS_PATH
        self._open_with_permission_redirect(REQUESTS_PATH, f"No requests file yet at:\n{REQUESTS_PATH}")

    def _open_playlists_file(self):
        root = self.cfg.get("playlists_path", "")
        if not root or not open_folder(root):
            messagebox.showwarning("Not found", f"Playlists folder not found yet at:\n{root or '(none set)'}")

    def _open_update_helper_file(self):
        # Ships next to the installed .exe (see installer.iss), not bundled
        # inside the frozen .exe's internal resources - so look there, not
        # via resource_path()/_MEIPASS. For a source run, install_dir() is
        # just the project root, where it already sits.
        candidate = os.path.join(install_dir(), "Update Helper.txt")
        self._open_with_permission_redirect(candidate, f"Couldn't find it at:\n{candidate}")

    def _uninstall_clicked(self):
        uninstallers = glob.glob(os.path.join(install_dir(), "unins*.exe"))
        if not uninstallers:
            messagebox.showinfo(
                "Uninstall",
                "No installer-generated uninstaller was found here. This button only "
                "works on a copy installed via the Media Downloader installer - "
                f"looked in:\n{install_dir()}"
            )
            return
        if not messagebox.askyesno(
            "Uninstall Media Downloader",
            "This will launch the uninstaller and close this app. Continue?"
        ):
            return
        try:
            subprocess.Popen([uninstallers[0]])
        except Exception as e:
            messagebox.showerror("Uninstall failed", str(e))
            return
        self.destroy()



def run():
    from core.bootstrap import bootstrap
    bootstrap()
    from core.startup_log import mark
    mark("run() called, showing splash screen")

    from gui.splash_screen import SplashScreen, MIN_DISPLAY_SECONDS
    splash = SplashScreen()

    # Splash and App() are each a full ctk.CTk() - i.e. each is its own
    # separate Tk root/interpreter, not a Toplevel of the other (there's
    # nothing to attach a Toplevel to before App() exists). Tkinter only
    # ever tracks ONE "default root" at a time (whichever Tk() was
    # created first/most recently), and CTkImage's internal PhotoImage
    # creation relies on that default root. Constructing App() - and,
    # critically, having it apply its own sidebar tab icons - WHILE
    # splash was still alive used to leave those PhotoImages registered
    # against the wrong (splash's) interpreter, which surfaced as every
    # sidebar tab icon (Download/Media/History/Settings/More/Version)
    # silently failing to appear, with `_apply_tab_icon`'s own
    # try/except swallowing a "image ... doesn't exist" TclError on
    # every single one. The same class of interpreter/default-root
    # confusion is also what produced the "Too early to create image:
    # no default root window" crashes later at runtime (see
    # crash_log.txt / _apply_network_status).
    #
    # The fix: never let two roots be alive at once. Run the splash
    # animation to completion and fully close() it FIRST - which clears
    # Tkinter's default-root pointer - and only THEN construct App(),
    # so App() is unambiguously the sole root for its entire life,
    # including every image it ever creates.
    while splash.elapsed_seconds() < MIN_DISPLAY_SECONDS:
        splash.animate_tick()
        splash.update()
        time.sleep(0.05)

    splash.close()
    mark("splash closed, constructing App()")
    app = App()
    mark("App() construction complete, entering mainloop()")
    app.mainloop()
    mark("mainloop() returned - app is closing")
