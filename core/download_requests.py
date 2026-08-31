"""
Download request tracking - the data system behind the Extras tab's
Request History and the retry-a-single-URL feature.

Every time the user clicks Download with a URL present, one "request" is
created. A request can contain one URL (single download) or many (batch
queue, playlist). Requests live in one of two states:

  requests_in_progress  - still downloading
  requests_completed    - every URL in the request has finished (success
                           OR failure - it moves here either way, since
                           the whole point of this system is dealing with
                           errors after the fact, not just successes)

Request ID format: "<type>-<n>", e.g. "video_queue_download-5", where n
is a running count of how many requests of that exact type have EVER
been created (not how many exist right now - deleting old history entries
doesn't reuse numbers).

Both dicts are persisted to a single JSON file so history survives a
restart. Kept as one file (not one-file-per-request) since the whole
dataset is small (a handful of KB even after hundreds of requests) and a
single read/write is simpler and faster than scanning a directory.
"""
import json
import os
import threading
import time
import uuid

from core.paths import app_dir

REQUESTS_DIR = os.path.join(app_dir(), "history")
REQUESTS_PATH = os.path.join(REQUESTS_DIR, "download_requests.json")

_lock = threading.RLock()

# --- O1: mtime-aware cache. update_item fires several times per queue item
# and each call used to fully parse + rewrite this file. Now the parsed
# store is kept in memory and only re-read when another process (the queue
# daemon) has actually touched the file. Writes stay immediate.
_cache = None
_cache_mtime = None

# When False (GUI "Save download info" toggle), start_request returns None
# and writes nothing; update_item / finish_request / add_item_to_request
# already no-op on an unknown request_id, so None flows through harmlessly.
_RECORDING = True


def set_recording(enabled):
    global _RECORDING
    _RECORDING = bool(enabled)


REQUEST_TYPES = {
    ("Video", "single"): "video_download",
    ("Audio", "single"): "audio_download",
    ("Video", "queue"): "video_queue_download",
    ("Audio", "queue"): "audio_queue_download",
    ("Video", "playlist"): "video_playlist_download",
    ("Audio", "playlist"): "audio_playlist_download",
}


def _default_store():
    return {"requests_in_progress": {}, "requests_completed": {}, "type_counters": {}}


def _disk_mtime():
    try:
        return os.path.getmtime(REQUESTS_PATH)
    except OSError:
        return None


def _read_disk():
    if os.path.exists(REQUESTS_PATH):
        try:
            with open(REQUESTS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("requests_in_progress", {})
            data.setdefault("requests_completed", {})
            data.setdefault("type_counters", {})
            return data
        except Exception:
            pass
    return _default_store()


def _load():
    """The parsed store. Re-read from disk only when the file changed since
    we last wrote it (i.e. the queue daemon touched it)."""
    global _cache, _cache_mtime
    mt = _disk_mtime()
    if _cache is None or mt != _cache_mtime:
        _cache = _read_disk()
        _cache_mtime = mt
    return _cache


def _save(data):
    global _cache, _cache_mtime
    os.makedirs(REQUESTS_DIR, exist_ok=True)
    tmp = REQUESTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, REQUESTS_PATH)
    _cache = data
    _cache_mtime = _disk_mtime()


def request_type_key(dtype, mode):
    """dtype: 'Video'/'Audio'. mode: 'single'/'queue'/'playlist'."""
    return REQUEST_TYPES.get((dtype, mode), f"{dtype.lower()}_{mode}_download")


def start_request(dtype, mode, urls, custom_name=None, out_dir=None):
    """Creates a new in-progress request for one or more URLs.
    urls: list of URL strings (order preserved). custom_name, if given
    (e.g. from the Batch Queue tab's optional rename field), is shown in
    the Extras tab's Request History instead of the first item's title -
    purely a display label, doesn't affect the request id scheme.
    Returns the new request_id, or None when recording is turned off."""
    if not _RECORDING:
        return None
    with _lock:
        data = _load()
        type_key = request_type_key(dtype, mode)
        data["type_counters"][type_key] = data["type_counters"].get(type_key, 0) + 1
        n = data["type_counters"][type_key]
        request_id = f"{type_key}-{n}"

        items = {}
        for url in urls:
            items[url] = {
                "name": None,
                "status": "pending",       # pending -> downloading -> success/failed
                "path": None,
                "error": None,
                "attempts": 0,
                "elapsed_seconds": None,
                "item_uid": str(uuid.uuid4()),
            }

        data["requests_in_progress"][request_id] = {
            "request_id": request_id,
            "type": type_key,
            "dtype": dtype,
            "mode": mode,
            "custom_name": custom_name or None,
            "out_dir": out_dir,
            "created_at": time.time(),
            "finished_at": None,
            "items": items,
            "elapsed_seconds": None,
        }
        _save(data)
        return request_id


def reset_stalled_downloads(new_status="pending", error=None):
    """Any item still marked 'downloading' in an in-progress request is
    stale on startup - nothing is actually downloading it right now (the
    process that was doing so died or was closed). Move each such item to
    `new_status`. The daemon calls this with 'pending' so its own
    interrupted work resumes; the GUI calls it with 'failed' so an
    interrupted item shows up as a clear, retryable failure instead of
    sitting frozen as 'downloading' forever. Returns the count changed."""
    affected = []
    with _lock:
        data = _load()
        changed = 0
        for rid, req in data["requests_in_progress"].items():
            hit = False
            for item in req["items"].values():
                if item.get("status") == "downloading":
                    item["status"] = new_status
                    if error is not None:
                        item["error"] = error
                    changed += 1
                    hit = True
            if hit:
                affected.append(rid)
        if changed:
            _save(data)
    # finish_request() takes _lock itself, so it must be called AFTER the
    # block above releases it - never from inside a `with _lock:`.
    if new_status in ("failed", "success", "skipped"):
        for rid in affected:
            finish_request(rid)
    return changed


def update_item(request_id, url, **fields):
    """Update one URL's status/fields within an in-progress request."""
    if request_id is None:
        return
    with _lock:
        data = _load()
        req = data["requests_in_progress"].get(request_id)
        if not req or url not in req["items"]:
            return
        req["items"][url].update(fields)
        _save(data)


def add_item_to_request(request_id, url):
    """Used by retry-from-history: appends a URL (typically one that
    already exists, being retried) as a fresh pending entry, or adds it if
    somehow missing. Works on completed requests too (retry re-opens them
    briefly, see retry_single_url in gui/app.py)."""
    if request_id is None:
        return
    with _lock:
        data = _load()
        for bucket in ("requests_in_progress", "requests_completed"):
            req = data[bucket].get(request_id)
            if req:
                req["items"].setdefault(url, {
                    "name": None, "status": "pending", "path": None,
                    "error": None, "attempts": 0, "elapsed_seconds": None,
                    "item_uid": str(uuid.uuid4()),
                })
                _save(data)
                return


def finish_request(request_id):
    """Moves a request from in_progress to completed once every item has
    a final status (success/failed). Computes the overall flag:
    'success' (all succeeded), 'partial' (mixed), or 'failed' (all failed).
    Safe to call even if some items are still 'pending'/'downloading' -
    it will simply not move the request yet in that case."""
    if request_id is None:
        return None
    with _lock:
        data = _load()
        req = data["requests_in_progress"].get(request_id)
        if not req:
            return None
        statuses = [item["status"] for item in req["items"].values()]
        if any(s in ("pending", "downloading") for s in statuses):
            _save(data)
            return None  # not done yet

        req["finished_at"] = time.time()
        if req["created_at"]:
            req["elapsed_seconds"] = req["finished_at"] - req["created_at"]

        if all(s in ("success", "skipped") for s in statuses):
            req["overall"] = "success"
        elif all(s == "failed" for s in statuses):
            req["overall"] = "failed"
        else:
            req["overall"] = "partial"

        data["requests_completed"][request_id] = req
        del data["requests_in_progress"][request_id]
        _save(data)
        return req["overall"]


def reopen_for_retry(request_id, url):
    """A single URL within an already-completed request is being retried.
    Moves the WHOLE request back to in_progress (with that one item reset
    to pending) so the normal finish_request() flow re-evaluates and
    re-files it once the retry concludes - this is what makes a retried
    request's dot correctly flip from red/orange back to green."""
    with _lock:
        data = _load()
        req = data["requests_completed"].get(request_id)
        if not req:
            req = data["requests_in_progress"].get(request_id)
            if not req:
                return
            if url in req["items"]:
                req["items"][url]["status"] = "pending"
                req["items"][url]["error"] = None
            _save(data)
            return
        if url in req["items"]:
            req["items"][url]["status"] = "pending"
            req["items"][url]["error"] = None
        req.pop("overall", None)
        req["finished_at"] = None
        data["requests_in_progress"][request_id] = req
        del data["requests_completed"][request_id]
        _save(data)


def get_all_requests():
    """Returns (in_progress_list, completed_list), each sorted so newest
    is first, for display in the Extras tab's Request History."""
    with _lock:
        data = _load()
        in_progress = sorted(data["requests_in_progress"].values(),
                              key=lambda r: r["created_at"], reverse=True)
        completed = sorted(data["requests_completed"].values(),
                            key=lambda r: r.get("finished_at") or r["created_at"], reverse=True)
    return in_progress, completed


def find_previous_download(url, out_dir):
    """Looks across every completed request for a prior SUCCESSFUL
    download of this exact URL into this exact output folder - the
    duplicate-download check (Settings > Advanced). Only successes count:
    a URL that previously failed here isn't a duplicate, it's still
    worth attempting. Returns the matching item dict (with its 'path',
    'name', etc) if found, else None."""
    if not url or not out_dir:
        return None
    out_dir_norm = os.path.normpath(os.path.abspath(out_dir))
    with _lock:
        data = _load()
        completed = list(data["requests_completed"].values())
    for req in completed:
        item = req["items"].get(url)
        if item and item.get("status") == "success" and item.get("path"):
            item_path = item["path"]
            if not os.path.isfile(item_path):
                continue  # the file's gone (moved/deleted) - not a real duplicate anymore
            item_dir = os.path.normpath(os.path.dirname(os.path.abspath(item_path)))
            if item_dir == out_dir_norm:
                return item
    return None


def get_request(request_id):
    with _lock:
        data = _load()
        return (data["requests_in_progress"].get(request_id)
                or data["requests_completed"].get(request_id))


def delete_request(request_id):
    with _lock:
        data = _load()
        data["requests_in_progress"].pop(request_id, None)
        data["requests_completed"].pop(request_id, None)
        _save(data)


def rename_request(request_id, new_name):
    """Sets/overwrites a request's custom_name - works on a request in
    either bucket (still in progress or already completed), so a
    request can be renamed at any point in its life, not just while
    it's still running. An empty new_name clears the custom name,
    falling back to the usual "first downloaded item's title" display
    logic (see gui/request_history.py's _first_title) rather than
    leaving a blank name displayed."""
    new_name = (new_name or "").strip()
    with _lock:
        data = _load()
        for bucket in ("requests_in_progress", "requests_completed"):
            req = data[bucket].get(request_id)
            if req is not None:
                if new_name:
                    req["custom_name"] = new_name
                else:
                    req.pop("custom_name", None)
                _save(data)
                return True
        return False
