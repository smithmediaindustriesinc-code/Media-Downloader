import json
import os
from core.paths import app_dir

CONFIG_PATH = os.path.join(app_dir(), "options", "config.json")

DEFAULT_CONFIG = {
    "schema_version": 1,   # bumped whenever a migration is added to _MIGRATIONS below
    "download_root": "",   # chosen default folder; Videos/ and Music/ live under it
    "video_path": "",
    "music_path": "",
    "playlists_path": "",
    "video_quality": "Best",
    "video_format": "mp4",
    "aspect_ratio": "Any",
    "audio_quality": "192",
    "audio_format": "mp3",
    "appearance_mode": "System",
    "color_theme": "blue",
    "font_family": "Segoe UI",
    "font_size": 13,
    "bold_text": False,
    "embed_thumbnail": True,
    "clipboard_watch": True,
    "default_playlist": False,
    "default_subtitles": False,
    "window_width": 820,   # remembers the size of the window when it was
    "window_height": 720,  # last closed; these two values are what a
                            # brand new install starts with (first-ever
                            # launch size), and get overwritten on every
                            # normal close after that.
    "window_x": None,      # last non-maximized position. Kept for the
    "window_y": None,      # settings-migration path; as of 1.6.0 a
                            # "Remembered" launch re-centers at the saved
                            # size rather than restoring this position.
    "window_maximized": False,  # was the window maximized at last close?
                            # A "Remembered" launch restores state("zoomed")
                            # from this; see gui/app.py _apply_launch_geometry
                            # / _on_close_requested. This is what fixes
                            # "remember last fullscreen" not sticking.
    "launch_resolution": "Fullscreen",  # "Fullscreen" = a true maximized window (default). "Remembered" = use window_width/
                            # height/x/y above. Otherwise one of the
                            # preset sizes computed from the primary
                            # monitor (see RESOLUTION_PRESETS in
                            # gui/app.py) - applied ONLY at launch, not
                            # live, and only overrides size, not position.
    "background_color": None,  # None = use the theme's own default background
    "log_box_height": 140,
    "dev_log_mode_enabled": False,
    "dev_request_mode_enabled": False,
    "dev_show_raw_ytdlp": False,
    "dev_notes_path": None,  # None = default location under app_dir()/options/
    "heartbeat_enabled": True,
    "heartbeat_interval_ms": 3000,
    "auto_save_enabled": True,
    "auto_save_interval_s": 5,
    "cookies_from_browser": "none",  # yt-dlp's actual fix for YouTube's bot
                            # challenge - "none" or one of "chrome",
                            # "firefox", "edge", "brave", "safari",
                            # "opera", "vivaldi" (yt-dlp's own supported
                            # browser names).
    "batch_delay_seconds": 3,  # pause between items in a batch/playlist
                            # download - spacing requests out is the
                            # other real mitigation against YouTube
                            # flagging rapid-fire requests as bot traffic.
    "playlist_fetch_timeout_s": 60,  # how long a playlist info lookup can
                            # hang before being treated as failed - see
                            # PlaylistFetchTimeout in core/downloader.py.
    "duplicate_detection_enabled": True,  # skip a URL if it was already
                            # successfully downloaded to the same output
                            # folder before - see find_previous_download
                            # in core/download_requests.py. Retrying a
                            # failed item from Request History always
                            # bypasses this check regardless of this
                            # setting - a retry is a deliberate re-attempt.
    "media_library_download_root_optout": False,  # the default download
                            # folder is auto-added to the Media Library
                            # (see gui/app.py _ensure_download_root_in_library);
                            # this flips True if the user removes it, so it
                            # isn't re-added on the next launch.
    "media_library_directories": [],  # folders the Media tab's Library
                            # subtab is allowed to scan - empty by
                            # default (nothing until the user configures
                            # it in Settings), not auto-populated, since
                            # someone managing lots of file types may
                            # well want folders this app never downloads
                            # into at all.
    "media_library_max_results": 200,  # hard cap on how many matches a
                            # Library search even looks for before
                            # stopping - the actual speed control (see
                            # core/media_library.py's scan_library).
    "media_library_include_subfolders": True,  # global toggle - when
                            # off, each configured folder's own
                            # subdirectories are skipped entirely
                            # rather than scanned into.
    "background_downloads_enabled": False,  # when closing the app with
                            # this on, if there's still pending queue
                            # work, a detached background process (see
                            # core/queue_daemon.py) finishes it after
                            # the GUI window is gone.
    "scroll_speed_ms": 8,  # Accessibility > Scroll Speed - lower means
                            # faster-feeling smooth scrolling (see
                            # gui/smooth_scroll.py's set_scroll_speed()).
    "loading_delay_enabled": False,  # Developer tab - a brief neutral
                            # overlay shown for loading_delay_ms on every
                            # tab switch, purely visual smoothing (see
                            # gui/sidebar_tabview.py's
                            # _maybe_show_loading_overlay). Off by
                            # default - a real, deliberate behavior
                            # change (every tab switch takes visibly
                            # longer), not something to force on anyone.
    "loading_delay_ms": 500,
    "dynamic_batch_queue_enabled": True,  # Advanced > Batch Queue -
                            # when on, the plain textbox is replaced by
                            # a scrollable list of individually-
                            # removable URL rows with undo support (see
                            # gui/app.py's _refresh_batch_dynamic_list). On by default as of 1.6.0.
    "save_download_info": True,  # Download tab toggle - when off, a
                            # download is NOT recorded to Request History
                            # or the History tab (core.download_requests /
                            # core.history recording is switched off). On
                            # by default. Added 1.6.4.
    "batch_prefetch_sizes": False,  # Advanced Settings toggle - when on,
                            # a batch/playlist download first runs a pass
                            # that fetches ONLY each item's file size
                            # (nothing else), and the queue + per-item
                            # ETAs then become size-based (remaining bytes
                            # / rolling avg speed) instead of
                            # item-count-based. OFF by default = exactly
                            # the pre-1.6.8 behaviour. Added 1.6.8.
    "app_update_include_beta": False,  # Version tab - when on, the in-app
                            # update check also offers preview/beta builds,
                            # not just the latest stable release. Off by
                            # default. Added 1.6.10.
}


# Current schema version - keep equal to DEFAULT_CONFIG["schema_version"].
CONFIG_SCHEMA_VERSION = DEFAULT_CONFIG["schema_version"]

# Numbered migrations. Each key N is a function that takes the config dict at
# schema version N-1 and mutates/returns it at version N. Add one whenever a
# setting is renamed, its shape changes, or an old value needs rewriting -
# so upgrades are explicit and testable rather than guessed at.
# Example (do not add unless real):
#   def _migrate_to_2(cfg):
#       cfg["new_name"] = cfg.pop("old_name", DEFAULT_CONFIG["new_name"])
#       return cfg
_MIGRATIONS = {
    # 2: _migrate_to_2,
}


def _run_migrations(cfg):
    """Steps cfg from its stored schema_version up to CONFIG_SCHEMA_VERSION,
    applying each numbered migration in order. Unknown/absent schema_version
    is treated as the current version for a config that already has all the
    current keys, or as 1 otherwise - i.e. we never run migrations that
    would corrupt a config that predates versioning but is otherwise fine."""
    current = cfg.get("schema_version")
    if not isinstance(current, int) or current < 1:
        current = 1
    for target in range(current + 1, CONFIG_SCHEMA_VERSION + 1):
        migrate = _MIGRATIONS.get(target)
        if migrate:
            try:
                cfg = migrate(cfg) or cfg
            except Exception:
                pass  # a failed migration must not brick startup
    cfg["schema_version"] = CONFIG_SCHEMA_VERSION
    return cfg


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(data)
            return _run_migrations(cfg)
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=4)
    os.replace(tmp, CONFIG_PATH)


# --------------------------------------------------------------------- #
# Settings export/import - built to survive version differences in both
# directions: an OLDER export (missing keys this version added since)
# imports cleanly with the new keys filled from defaults, and a NEWER
# export (containing keys an older version of the app doesn't know
# about yet) doesn't error out either - those extra keys are preserved
# as-is rather than dropped, in case the app is later updated and they
# become meaningful again. Nothing about the export format REQUIRES
# every setting to be present - it's a plain flat JSON object, and any
# subset of recognized keys imports correctly.
# --------------------------------------------------------------------- #
EXPORT_FORMAT_VERSION = 1


def export_config_dict(cfg, include_history=True):
    """What actually gets written to an export file - the live settings
    plus a small metadata block, plus (by default) download history and
    request history, so moving to a new install/machine is as seamless
    as possible rather than just carrying over preferences. The
    metadata is informational only (shown in Import's report) and is
    never required for a later import to work - a hand-edited or very
    old file with none of it still imports fine via
    merge_imported_config().

    Playlists and Media Library folders aren't duplicated here as data -
    they're real files/folders on disk, not something that belongs in a
    JSON export - but the PATHS to them (playlists_path,
    media_library_directories) are already part of `cfg` itself, so
    pointing a fresh install at the same locations naturally picks them
    back up without needing to copy anything through this file."""
    from core.app_info import APP_VERSION
    export = {
        "_export_format_version": EXPORT_FORMAT_VERSION,
        "_exported_by_app_version": APP_VERSION,
        "settings": cfg,
    }
    if include_history:
        try:
            from core.history import load_history
            export["history"] = load_history()
        except Exception:
            pass  # history is a bonus in this export, never worth failing the whole export over
        try:
            from core.download_requests import _load as _load_requests
            export["download_requests"] = _load_requests()
        except Exception:
            pass
    return export


def merge_imported_config(imported):
    """Takes whatever was loaded from an imported settings file (any
    shape - a full export with the "_export_format_version"/"settings"
    wrapper, a bare flat settings dict from an older version that
    predates that wrapper, or even just a hand-edited partial JSON file
    with only a couple of keys in it) and produces a safe, complete
    config to actually use.

    Returns (merged_cfg, report) where report is a list of short,
    human-readable strings describing what happened - shown to the user
    after import so partial/incompatible imports are never silent.
    Never raises for a missing or extra key; only raises if `imported`
    isn't usable as a settings source at all (not a dict)."""
    if not isinstance(imported, dict):
        raise ValueError("That file doesn't look like a settings export (not a JSON object).")

    report = []

    # Unwrap the "_export_format_version"/"settings" wrapper if present;
    # otherwise treat the whole thing as a flat settings dict directly -
    # this is what makes an export from BEFORE this wrapper existed
    # still import correctly.
    if "settings" in imported and isinstance(imported["settings"], dict):
        source_version = imported.get("_exported_by_app_version", "unknown")
        report.append(f"Imported from app version {source_version}.")
        flat = imported["settings"]
    else:
        report.append("Imported from an older export format (no version info included) - "
                       "treated as a plain settings file.")
        flat = imported

    merged = DEFAULT_CONFIG.copy()
    missing = [k for k in DEFAULT_CONFIG if k not in flat]
    unknown = [k for k in flat if k not in DEFAULT_CONFIG]
    type_mismatches = []

    for key, value in flat.items():
        if key not in DEFAULT_CONFIG:
            # A setting from a NEWER version of the app that this version
            # doesn't recognize yet - preserved as-is rather than
            # dropped, so it survives being re-exported later even
            # though nothing in this version's UI uses it.
            merged[key] = value
            continue
        default_value = DEFAULT_CONFIG[key]
        if default_value is not None and not isinstance(value, type(default_value)):
            # A genuine type mismatch (e.g. a setting that changed shape
            # between versions) - keep the safe default rather than risk
            # the app breaking on a value it doesn't expect, but tell the
            # user this specific setting didn't carry over.
            type_mismatches.append(key)
            continue
        merged[key] = value

    if missing:
        report.append(f"{len(missing)} setting(s) not in the imported file - kept at current/default values.")
    if unknown:
        report.append(f"{len(unknown)} setting(s) in the file aren't recognized by this version - "
                       f"kept as-is in case a future update uses them.")
    if type_mismatches:
        report.append(f"{len(type_mismatches)} setting(s) had an incompatible value and were skipped: "
                       + ", ".join(type_mismatches))
    if not missing and not unknown and not type_mismatches:
        report.append("Every setting in the file matched this version exactly.")

    # History/request history are MERGED into whatever's already on this
    # install, not a destructive replace - existing entries are never
    # lost, imported ones that aren't already present (matched by their
    # own id) get added. This is what makes moving to a fresh install
    # "seamless" per how this was asked for, without risking wiping out
    # anything already here (e.g. importing an OLD export by mistake
    # after already using the new install for a while).
    if "history" in imported and isinstance(imported["history"], list):
        try:
            from core.history import load_history, _save as _save_history
            current = load_history()
            existing_ids = {e.get("id") for e in current}
            added = [e for e in imported["history"] if e.get("id") not in existing_ids]
            if added:
                merged_history = (current + added)[:300]  # keep history.py's own MAX_ENTRIES cap
                _save_history(merged_history)
            report.append(f"Merged {len(added)} history entr{'y' if len(added) == 1 else 'ies'} "
                           f"({len(imported['history']) - len(added)} already present, skipped).")
        except Exception as e:
            report.append(f"Could not merge history: {e}")

    if "download_requests" in imported and isinstance(imported["download_requests"], dict):
        try:
            from core.download_requests import _load as _load_requests, _save as _save_requests
            current = _load_requests()
            added_count = 0
            for bucket in ("requests_in_progress", "requests_completed"):
                imported_bucket = imported["download_requests"].get(bucket, {})
                current_bucket = current.setdefault(bucket, {})
                for request_id, req in imported_bucket.items():
                    if request_id not in current_bucket:
                        current_bucket[request_id] = req
                        added_count += 1
            # type_counters need merging too (take the higher of the two
            # per type) so future new requests don't collide with
            # imported ones sharing the same id scheme.
            imported_counters = imported["download_requests"].get("type_counters", {})
            current_counters = current.setdefault("type_counters", {})
            for type_key, count in imported_counters.items():
                current_counters[type_key] = max(current_counters.get(type_key, 0), count)
            if added_count:
                _save_requests(current)
            report.append(f"Merged {added_count} request(s) from the imported file.")
        except Exception as e:
            report.append(f"Could not merge request history: {e}")

    merged = _run_migrations(merged)
    return merged, report


def check_and_apply_pending_import():
    """Checked once at app startup (see gui/app.py's App.__init__). If
    the installer staged a settings file to import (see installer.iss's
    CurStepChanged - offered only on a genuinely fresh install, never
    an upgrade over existing settings), this picks it up, merges it in
    via merge_imported_config() (same merge behavior as the in-app
    Import Settings button - nothing is silently overwritten), and then
    deletes the marker file so it's only ever applied once, not on
    every subsequent launch. Returns (applied, report) - applied is
    False (report is []) when there was nothing pending, so callers can
    tell "nothing to do" apart from "import happened, here's what
    changed"."""
    pending_path = os.path.join(app_dir(), "options", "pending_import.json")
    if not os.path.exists(pending_path):
        return False, []
    try:
        with open(pending_path, "r") as f:
            imported = json.load(f)
        merged, report = merge_imported_config(imported)
        save_config(merged)
        return True, report
    except Exception as e:
        return False, [f"Could not apply the staged import: {e}"]
    finally:
        try:
            os.remove(pending_path)
        except OSError:
            pass  # never worth blocking startup over a leftover marker file
