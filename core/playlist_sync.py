"""F10 (1.7.4): incremental playlist sync. Remembers which entry ids of a
playlist URL were already fetched, so a later "Sync" run only downloads the
new ones. Distinct from resuming an interrupted batch.

Store: <app_dir>/playlist_sync.json = {normalized_url: {"ids": [...],
"title": str, "last": iso8601, "count": int}}
"""
import datetime
import json
import os
import threading

from core.paths import app_dir

_PATH = os.path.join(app_dir(), "playlist_sync.json")
_lock = threading.Lock()


def _norm(url):
    return (url or "").strip().split("&")[0]  # drop trailing &index=… etc


def _load():
    if os.path.exists(_PATH):
        try:
            with open(_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def _save(d):
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, _PATH)


def get_record(playlist_url):
    return _load().get(_norm(playlist_url))


def is_known(playlist_url):
    return _norm(playlist_url) in _load()


def new_entries(playlist_url, entries):
    """entries: list of {"id","url","title"} from fetch_playlist_info.
    Returns the subset whose id isn't already recorded. If the playlist was
    never synced, returns all entries."""
    rec = get_record(playlist_url)
    if not rec:
        return list(entries)
    seen = set(rec.get("ids") or [])
    return [e for e in entries if str(e.get("id") or e.get("url")) not in seen]


def record(playlist_url, entries, title=""):
    """Merge these entries' ids into the stored fingerprint for this URL."""
    with _lock:
        d = _load()
        key = _norm(playlist_url)
        rec = d.get(key) or {"ids": []}
        ids = set(rec.get("ids") or [])
        for e in entries:
            ids.add(str(e.get("id") or e.get("url")))
        d[key] = {
            "ids": sorted(ids),
            "title": title or rec.get("title", ""),
            "last": datetime.datetime.now().isoformat(timespec="seconds"),
            "count": len(ids),
        }
        _save(d)
        return d[key]


def forget(playlist_url):
    with _lock:
        d = _load()
        if _norm(playlist_url) in d:
            del d[_norm(playlist_url)]
            _save(d)
            return True
    return False
