"""
Saves the contents of the Download tab's text fields (URL, name, batch
queue, etc) as part of the regular auto-save cycle, so closing the app
mid-thought and reopening it picks back up where you left off - but
only within a reasonable window. If more than an hour has passed since
it was last saved, the draft is treated as stale and discarded rather
than restored, since a URL/name typed an hour-plus ago is more likely
to be leftover clutter than something still wanted.
"""
import json
import os
import time

from core.paths import app_dir

DRAFT_STATE_PATH = os.path.join(app_dir(), "options", "draft_state.json")
MAX_AGE_SECONDS = 60 * 60  # 1 hour


def save_draft_state(fields):
    """fields: a plain dict of {field_name: text_value}. Writes it with
    the current timestamp, overwriting whatever was there before - this
    is called on every auto-save tick, so it always reflects the most
    recent state of the text fields."""
    try:
        os.makedirs(os.path.dirname(DRAFT_STATE_PATH), exist_ok=True)
        with open(DRAFT_STATE_PATH, "w") as f:
            json.dump({"saved_at": time.time(), "fields": fields}, f)
    except OSError:
        pass  # a failed draft save is never worth interrupting anything over


def load_draft_state():
    """Returns the saved fields dict if a draft exists and is still
    within the 1-hour window, else None - and in the "exists but stale"
    case, deletes the file so it doesn't linger and get mistaken for
    fresh data later. Never raises - a corrupted or unreadable draft
    file is treated the same as no draft at all."""
    if not os.path.exists(DRAFT_STATE_PATH):
        return None
    try:
        with open(DRAFT_STATE_PATH, "r") as f:
            data = json.load(f)
        saved_at = data.get("saved_at", 0)
        age = time.time() - saved_at
        if age > MAX_AGE_SECONDS:
            clear_draft_state()
            return None
        return data.get("fields", {})
    except (OSError, ValueError, KeyError):
        return None


def clear_draft_state():
    try:
        if os.path.exists(DRAFT_STATE_PATH):
            os.remove(DRAFT_STATE_PATH)
    except OSError:
        pass
