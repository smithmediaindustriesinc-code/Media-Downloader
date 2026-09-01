"""F9 (1.7.4): channel / playlist auto-monitor. Subscribe to any
yt-dlp-supported channel or playlist URL; a periodic check finds uploads not
seen on the previous check.

Subscriptions live in cfg["monitor_subscriptions"] as a list of dicts:
  {id, url, name, seen_ids:[...], last_check_iso, auto_download:bool,
   dtype:"Video"|"Audio"}
This module only computes the diff and updates seen_ids - the GUI decides
what to do with the new entries (enqueue vs. just notify) and persists cfg.
"""
import datetime
import uuid


def make_subscription(url, name="", dtype="Video", auto_download=False):
    return {
        "id": uuid.uuid4().hex[:12],
        "url": (url or "").strip(),
        "name": (name or "").strip() or (url or "").strip(),
        "seen_ids": [],
        "last_check_iso": "",
        "auto_download": bool(auto_download),
        "dtype": dtype if dtype in ("Video", "Audio") else "Video",
    }


def check_subscription(sub, timeout_s=45, cancel_event=None):
    """Return (new_entries, error). new_entries: list of {id,url,title} not in
    sub['seen_ids']. On the FIRST check (no seen_ids) everything is recorded as
    seen and [] is returned - a fresh subscription shouldn't dump the whole
    back-catalogue into the queue."""
    from core.downloader import fetch_playlist_info
    try:
        info = fetch_playlist_info(sub["url"], timeout_seconds=timeout_s,
                                   cancel_event=cancel_event)
    except Exception as e:  # noqa: BLE001
        return [], str(e)

    entries = info.get("entries") or []
    ids = [str(e.get("id") or e.get("url")) for e in entries]
    first_run = not sub.get("seen_ids")
    seen = set(sub.get("seen_ids") or [])
    new = [e for e in entries if str(e.get("id") or e.get("url")) not in seen]

    sub["seen_ids"] = sorted(set(ids) | seen)
    sub["last_check_iso"] = datetime.datetime.now().isoformat(timespec="seconds")
    if not sub.get("name") or sub["name"] == sub["url"]:
        sub["name"] = info.get("playlist_title") or sub["url"]

    return ([] if first_run else new), None
