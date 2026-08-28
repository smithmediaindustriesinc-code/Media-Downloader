import os
import re
import platform
import shutil
import subprocess


def open_folder(path):
    """Open a folder in the OS file manager, cross-platform."""
    if not path or not os.path.isdir(path):
        return False
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(path)  # noqa
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


def open_file(path):
    """Open a single file with the OS's default application for it
    (Notepad for .txt, etc), cross-platform. Returns (ok, message) -
    message is empty on success. Specifically distinguishes a
    permissions problem from any other failure, since that's something
    the user can actually act on (see gui/app.py's
    _open_with_permission_redirect, which offers to open the file's
    containing folder so they can grant access themselves - e.g. via
    right-click > Properties > Security, or Run as Administrator - or
    on macOS/Linux, adjust permissions from the file manager) rather
    than just reporting a generic, unhelpful failure."""
    if not path or not os.path.isfile(path):
        return False, "File not found."
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(path)  # noqa
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True, ""
    except PermissionError:
        return False, "permission_denied"
    except Exception as e:
        return False, str(e)


def list_files(folder):
    """Flat list of files (not folders) in a directory."""
    if not os.path.isdir(folder):
        return []
    return sorted(
        f for f in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, f))
    )


def move_files(src_folder, dest_folder, filenames):
    """Move the given filenames from src_folder to dest_folder.
    Returns (moved: list, failed: dict[name->error])."""
    os.makedirs(dest_folder, exist_ok=True)
    moved, failed = [], {}
    for name in filenames:
        src = os.path.join(src_folder, name)
        dest = os.path.join(dest_folder, name)
        try:
            if os.path.exists(dest):
                base, ext = os.path.splitext(name)
                counter = 1
                while os.path.exists(dest):
                    dest = os.path.join(dest_folder, f"{base} ({counter}){ext}")
                    counter += 1
            shutil.move(src, dest)
            moved.append(name)
        except Exception as e:
            failed[name] = str(e)
    return moved, failed


def make_unique_name(out_dir, name, ext):
    """Return a filename (without extension) that doesn't collide with an
    existing file in out_dir, appending (1), (2), ... as needed."""
    candidate = name
    counter = 1
    while os.path.exists(os.path.join(out_dir, f"{candidate}.{ext}")):
        candidate = f"{name} ({counter})"
        counter += 1
    return candidate


def format_file_size(num_bytes):
    """Auto-scales through B/KB/MB/GB/TB - whenever the value would be
    over 1000 of the current unit, moves up a unit and divides by 1000,
    per how this was specifically asked for (decimal 1000 scaling, not
    binary 1024, and applying uniformly all the way up to TB rather than
    stopping at a fixed MB/GB pair)."""
    if num_bytes is None:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    unit_index = 0
    while size >= 1000 and unit_index < len(units) - 1:
        size /= 1000
        unit_index += 1
    return f"{size:.2f} {units[unit_index]}"


def beautify_title(title):
    """Cleans up a fetched video/audio title before it becomes a
    filename: strips a leading "[Video]" prefix some sites/uploads
    include (not meaningful as part of the actual name), and replaces
    underscores with spaces so "my_video_title" reads as "my video
    title" - done here, once, so every call site that turns a fetched
    title into a download name gets this consistently rather than each
    one needing to remember to apply it."""
    if not title:
        return title
    cleaned = re.sub(r"^\s*\[video\]\s*", "", title, flags=re.IGNORECASE)
    cleaned = cleaned.replace("_", " ")
    return cleaned.strip() or title


def strip_leading_special(text):
    """For SORTING only (never for search - see weighted_match_score,
    which deliberately does NOT use this, so searching ".com" or ".org"
    still works): drops any leading characters that aren't a letter or
    digit, so "[Cool] Video" alphabetizes under "C" rather than "[". If
    the whole string is special characters, returns it unchanged rather
    than stripping down to nothing."""
    if not text:
        return text
    i = 0
    while i < len(text) and not text[i].isalnum():
        i += 1
    return text[i:] if i < len(text) else text


def weighted_match_score(query, name, url=None, extra_fields=None):
    """Multi-field search with tiered priority: a match in `name` always
    outranks a match in `url`, which always outranks a match in any of
    `extra_fields` (a list of secondary strings - status, error message,
    request id, file type, whatever else is relevant to search) -
    regardless of how strong the individual text match is within each
    tier. Within a tier, exact match > starts-with > substring > partial
    word overlap, same scheme as core/media_library.py's relevance
    scoring. Returns 0 for no match anywhere, or a positive score where
    higher is always a better match AND always tier-correct (any name
    match outscores any url match outscores any extra-field match).

    Deliberately does NOT strip leading special characters the way
    strip_leading_special() does for sorting - a search for ".com" or
    ".org" needs to actually match text containing those literal
    characters, which stripping would break."""
    query = (query or "").strip().lower()
    if not query:
        return 1  # browsing with no search text - everything "matches" equally

    def _field_score(text):
        if not text:
            return 0
        text_lower = text.lower()
        if text_lower == query:
            return 40
        if text_lower.startswith(query):
            return 30
        if query in text_lower:
            return 20
        words = query.split()
        if words:
            hits = sum(1 for w in words if w in text_lower)
            if hits:
                return int(10 * hits / len(words))
        return 0

    name_score = _field_score(name)
    if name_score:
        return 200 + name_score  # tier 3 (highest): 200-240

    url_score = _field_score(url)
    if url_score:
        return 100 + url_score  # tier 2: 100-140

    for field in (extra_fields or []):
        field_score = _field_score(field)
        if field_score:
            return field_score  # tier 1 (lowest): 0-40

    return 0


def sanitize_filename(name):
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        name = name.replace(ch, "_")
    return name.strip() or "download"
