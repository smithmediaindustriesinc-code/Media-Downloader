import json
import os
import uuid
import datetime
from core.paths import app_dir

HISTORY_PATH = os.path.join(app_dir(), "history", "history.json")
MAX_ENTRIES = 300

# When False (set from the GUI's "Save download info" toggle), add_entry
# is a no-op that returns None and update_entry silently does nothing, so a
# download leaves no trace in history. Reads (load_history) are unaffected.
_RECORDING = True


def set_recording(enabled):
    global _RECORDING
    _RECORDING = bool(enabled)


def load_history():
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r") as f:
                history = json.load(f)
        except Exception:
            return []
    else:
        history = []

    # Migrate any entries saved before per-entry IDs existed, so old
    # history.json files can still be deleted from individually.
    changed = False
    for entry in history:
        if "id" not in entry:
            entry["id"] = str(uuid.uuid4())
            changed = True
    if changed:
        _save(history)
    return history


def _save(history):
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    tmp = HISTORY_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(history, f, indent=2)
    os.replace(tmp, HISTORY_PATH)


def add_entry(url, name, dtype, path, status="Success"):
    """Creates a new history entry and returns its id - callers that
    want to update this SAME entry later (rather than creating a
    duplicate one) as a download progresses through stages should hold
    onto that id and pass it to update_entry().

    Returns None (and writes nothing) when recording is turned off - all
    the update_entry(None, ...) calls that follow are then harmless."""
    if not _RECORDING:
        return None
    history = load_history()
    entry_id = str(uuid.uuid4())
    history.insert(0, {
        "id": entry_id,
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "type": dtype,
        "name": name,
        "url": url,
        "path": path,
        "status": status,
    })
    history = history[:MAX_ENTRIES]
    _save(history)
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
    history = load_history()
    for entry in history:
        if entry.get("id") == entry_id:
            entry.update(fields)
            _save(history)
            return True
    return False


def delete_entry(entry_id):
    """Remove a single history entry by its id. Returns True if something
    was actually removed."""
    history = load_history()
    new_history = [e for e in history if e.get("id") != entry_id]
    removed = len(new_history) != len(history)
    if removed:
        _save(new_history)
    return removed


def clear_history():
    _save([])
