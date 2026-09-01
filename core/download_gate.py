"""1.7.4 pre-download gating:
  F6  should_skip()      - duration / size / resolution / already-in-library
  F7  repeat_url_entry() - has this exact URL been downloaded before?
  F16 budget_state()     - monthly data budget status
"""
import os

from core.history import load_history
from core.download_stats import bytes_this_month


def should_skip(url, cfg, media_info=None, size_bytes=None, out_dir=None):
    """(skip: bool, reason: str). Only active when cfg['skip_rules_enabled'].
    media_info: yt-dlp info dict (duration, height); size_bytes: prefetched
    size if available."""
    if not cfg.get("skip_rules_enabled"):
        return False, ""
    info = media_info or {}
    dur = info.get("duration")
    short = cfg.get("skip_shorter_than_s", 0) or 0
    long = cfg.get("skip_longer_than_s", 0) or 0
    if dur and short and dur < short:
        return True, f"shorter than {short}s ({int(dur)}s)"
    if dur and long and dur > long:
        return True, f"longer than {long}s ({int(dur)}s)"

    max_mb = cfg.get("skip_larger_than_mb", 0) or 0
    if max_mb and size_bytes and size_bytes > max_mb * 1024 * 1024:
        return True, f"larger than {max_mb} MB ({size_bytes // (1024 * 1024)} MB)"

    min_h = cfg.get("skip_min_height", 0) or 0
    if min_h:
        h = info.get("height") or _max_format_height(info)
        if h and h < min_h:
            return True, f"below {min_h}px ({h}px)"

    if cfg.get("skip_if_in_library"):
        title = info.get("title") or ""
        if title and _in_library(title, cfg):
            return True, "already in your Media Library"
    return False, ""


def _max_format_height(info):
    best = 0
    for f in info.get("formats") or []:
        h = f.get("height") or 0
        if h > best:
            best = h
    return best


def _in_library(title, cfg):
    from core.utils import sanitize_filename
    stem = sanitize_filename(title).lower()[:60]
    if not stem:
        return False
    for d in cfg.get("media_library_directories") or []:
        try:
            for root, _dirs, files in os.walk(d):
                for fn in files:
                    if stem in os.path.splitext(fn)[0].lower():
                        return True
                if not cfg.get("media_library_include_subfolders", True):
                    break
        except OSError:
            continue
    return False


def repeat_url_entry(url):
    """F7: the most recent successful History entry for this exact URL, or
    None. The GUI warns before starting; batch/playlist already have their own
    same-folder duplicate check."""
    if not url:
        return None
    for e in load_history():
        if e.get("url") == url and e.get("status") == "Success":
            return e
    return None


def budget_state(cfg):
    """(over: bool, used_bytes: int, cap_bytes: int). cap 0 -> disabled
    (over is always False)."""
    gb = cfg.get("bandwidth_budget_gb", 0) or 0
    if gb <= 0:
        return False, 0, 0
    cap = int(gb * 1024 * 1024 * 1024)
    used = bytes_this_month()
    return used >= cap, used, cap
