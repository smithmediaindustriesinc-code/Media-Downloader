import atexit
import json
import os
import threading
import uuid
import datetime
from core.paths import app_dir

HISTORY_PATH = os.path.join(app_dir(), "history", "history.json")
MAX_ENTRIES = 300

# When False (set from the GUI's "Save download info" toggle), add_entry
# is a no-op that returns None and update_entry silently does nothing, so a
# download leaves no trace in history. Reads (load_history) are unaffected.
_RECORDING = True

# --- O1: in-memory cache -------------------------------------------------
# Every add_entry / update_entry used to do a full disk read + parse AND a
# full rewrite; a batch download fired hundreds of those on the worker
# thread, and _do_refresh_history_tab re-parsed the file every 250ms. Now
# the list lives in memory and reads are free. Writes stay immediate, but
# _write_now merges in any entries another process (the queue daemon)
# appended so nothing is lost across the GUI<->daemon handoff.
_lock = threading.RLock()
_cache = None                 # list once loaded, else None
_deleted_ids = set()          # ids deleted here - never resurrect them from disk
_last_written_mtime = None     # mtime of our own last write - lets us skip the
#                                merge-read unless another process touched the file


def set_recording(enabled):
    global _RECORDING
    _RECORDING = bool(enabled)


def _read_disk():
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            history = json.load(f)
        return history if isinstance(history, list) else []
    except Exception:
        return []


def _ensure_loaded():
    global _cache
    if _cache is not None:
        return
    history = _read_disk()
    changed = False
    for entry in history:
        if "id" not in entry:
            entry["id"] = str(uuid.uuid4())
            changed = True
    _cache = history
    if changed:
        _write_now()


def _disk_mtime():
    try:
        return os.path.getmtime(HISTORY_PATH)
    except OSError:
        return None


def _write_now():
    global _last_written_mtime
    if _cache is None:
        return
    # Only pay for the merge-read if another process (the queue daemon)
    # touched the file since our last write.
    if _disk_mtime() != _last_written_mtime:
        disk = _read_disk()
        have = {e.get("id") for e in _cache}
        extra = [e for e in disk
                 if e.get("id") not in have and e.get("id") not in _deleted_ids]
        if extra:
            _cache[:] = (_cache + extra)[:MAX_ENTRIES]
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    tmp = HISTORY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_cache, f, indent=2)
    os.replace(tmp, HISTORY_PATH)
    _last_written_mtime = _disk_mtime()


def flush_history():
    """Write any pending history to disk now. Registered with atexit and
    also called from the app's close handler as a safety net."""
    try:
        with _lock:
            if _cache is not None:
                _write_now()
    except Exception:
        pass


atexit.register(flush_history)


def load_history():
    """A copy of the current history (newest-first), safe for the caller to
    read or mutate without affecting the cache."""
    with _lock:
        _ensure_loaded()
        return list(_cache)


def _save(history):
    """Back-compat shim: replace the whole cache and persist."""
    global _cache
    with _lock:
        _cache = list(history)
        _write_now()


def add_entry(url, name, dtype, path, status="Success"):
    """Creates a new history entry and returns its id - callers that
    want to update this SAME entry later (rather than creating a
    duplicate one) as a download progresses through stages should hold
    onto that id and pass it to update_entry().

    Returns None (and writes nothing) when recording is turned off - all
    the update_entry(None, ...) calls that follow are then harmless."""
    if not _RECORDING:
        return None
    with _lock:
        _ensure_loaded()
        entry_id = str(uuid.uuid4())
        _cache.insert(0, {
            "id": entry_id,
            "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "type": dtype,
            "name": name,
            "url": url,
            "path": path,
            "status": status,
        })
        del _cache[MAX_ENTRIES:]
        _write_now()
        return entry_id


def update_entry(entry_id, **fields):
    """Updates an existing history entry in place - used to move a
    single entry through its actual stages (Analyzing -> In Progress ->
    Success/Failed) rather than creating a new entry for each stage.
    Silently does nothing if entry_id doesn't exist (e.g. history was
    cleared mid-download) rather than erroring - this is always called
    from a background download thread where raising would be awkward to
    handle usefully."""
    if entry_id is None or not _RECORDING:
        return False
    with _lock:
        _ensure_loaded()
        for entry in _cache:
            if entry.get("id") == entry_id:
                entry.update(fields)
                _write_now()
                return True
    return False


def delete_entry(entry_id):
    """Remove a single history entry by its id. Returns True if something
    was actually removed."""
    with _lock:
        _ensure_loaded()
        before = len(_cache)
        _cache[:] = [e for e in _cache if e.get("id") != entry_id]
        removed = len(_cache) != before
        if removed:
            _deleted_ids.add(entry_id)
            _write_now()
        return removed


def set_tags(entry_id, tags):
    """F12 (1.7.4): replace an entry's free-form tag list. Tags are trimmed,
    de-duped (case-insensitive), empty ones dropped."""
    if entry_id is None:
        return False
    seen, clean = set(), []
    for t in tags or []:
        t = str(t).strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            clean.append(t)
    with _lock:
        _ensure_loaded()
        for entry in _cache:
            if entry.get("id") == entry_id:
                if clean:
                    entry["tags"] = clean
                else:
                    entry.pop("tags", None)
                _write_now()
                return True
    return False


def all_tags():
    """Every distinct tag currently in use, sorted."""
    with _lock:
        _ensure_loaded()
        seen = {}
        for e in _cache:
            for t in e.get("tags") or []:
                seen.setdefault(t.lower(), t)
    return [seen[k] for k in sorted(seen)]


def clear_history():
    global _cache, _deleted_ids, _last_written_mtime
    with _lock:
        _deleted_ids |= {e.get("id") for e in (_cache or [])}
        _cache = []
        os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
        tmp = HISTORY_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2)
        os.replace(tmp, HISTORY_PATH)
        _last_written_mtime = _disk_mtime()
