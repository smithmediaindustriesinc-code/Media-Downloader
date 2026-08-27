"""
The Media tab's Library subtab - scans a user-configured list of
directories and lets them search across EVERY kind of file they manage,
not just the video/audio this app downloads. Built with the mindset of
someone managing a lot of different file types: broad category
detection (video, audio, images, documents, archives, code, other), and
a hard cap on how many matches it even looks for before stopping - the
actual speed mechanism, not sorting a huge scan after the fact.
"""
import os
import time
import hashlib

CATEGORY_EXTENSIONS = {
    "Video": {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".wmv", ".m4v", ".mpg", ".mpeg", ".ts"},
    "Audio": {".mp3", ".wav", ".m4a", ".flac", ".opus", ".aac", ".ogg", ".wma"},
    "Image": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".tiff", ".heic"},
    "Document": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".xls", ".xlsx", ".ppt", ".pptx",
                 ".csv", ".md"},
    "Archive": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Code": {".py", ".js", ".html", ".css", ".json", ".xml", ".java", ".c", ".cpp", ".sh", ".bat"},
}
CATEGORIES = ["All"] + list(CATEGORY_EXTENSIONS.keys()) + ["Other"]

SORT_MODES = ["Best match", "Name (A-Z)", "Name (Z-A)", "Date modified (newest)",
              "Date modified (oldest)", "Largest first", "Smallest first", "Duplicates only"]


def categorize(filename):
    ext = os.path.splitext(filename)[1].lower()
    for category, extensions in CATEGORY_EXTENSIONS.items():
        if ext in extensions:
            return category
    return "Other"


def _relevance_score(filename, query):
    """Higher = better match. Cheap and deliberately simple (no fuzzy-
    matching library dependency) - exact name match scores highest,
    then starts-with, then the query appearing anywhere, then falls back
    to how many of the query's individual words show up at all. 0 means
    no match."""
    name_lower = filename.lower()
    query_lower = query.lower().strip()
    if not query_lower:
        return 1  # browsing with no search text - everything matches equally
    stem = os.path.splitext(name_lower)[0]
    if stem == query_lower:
        return 100
    if stem.startswith(query_lower):
        return 80
    if query_lower in name_lower:
        return 60
    words = query_lower.split()
    if words:
        hits = sum(1 for w in words if w in name_lower)
        if hits:
            return 20 + (20 * hits / len(words))
    return 0


def scan_library(directories, query="", category_filter="All", max_results=200, include_subfolders=True):
    """Walks every directory in `directories` (skipping ones that don't
    exist - never an error, same philosophy as the rest of this app's
    filesystem-backed features), scoring and collecting matches, and
    STOPS as soon as max_results matches have been found - this is the
    actual speed control, not a cap applied after scanning everything.
    include_subfolders=False restricts each directory to its own
    top-level files only, skipping recursion entirely - the global
    toggle in Settings > Files that governs the whole Media Library at
    once, not a per-folder setting.
    Returns a list of dicts: name, path, category, size, modified
    (unix timestamp), score - sorted by score descending (best match
    first) by default; callers apply a different sort afterward if the
    user picked one via the Sort dropdown.
    """
    results = []
    seen_paths = set()
    for directory in directories or []:
        if not directory or not os.path.isdir(directory):
            continue
        for root, dirs, files in os.walk(directory):
            # Skip hidden/system-ish folders (dotfolders) - not useful to
            # surface in a media library and often huge (.git, etc).
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            if not include_subfolders:
                dirs[:] = []  # os.walk won't descend into anything left in dirs after this
            for filename in files:
                if filename.startswith("."):
                    continue
                full_path = os.path.join(root, filename)
                if full_path in seen_paths:
                    continue  # the same directory (or a symlinked one) listed twice
                category = categorize(filename)
                if category_filter != "All" and category != category_filter:
                    continue
                score = _relevance_score(filename, query)
                if query and score == 0:
                    continue
                try:
                    stat = os.stat(full_path)
                except OSError:
                    continue
                seen_paths.add(full_path)
                results.append({
                    "name": filename,
                    "path": full_path,
                    "category": category,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "score": score,
                })
                if len(results) >= max_results:
                    results.sort(key=lambda r: r["score"], reverse=True)
                    return results
    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def sort_results(results, sort_mode):
    if sort_mode == "Name (A-Z)":
        return sorted(results, key=lambda r: r["name"].lower())
    if sort_mode == "Name (Z-A)":
        return sorted(results, key=lambda r: r["name"].lower(), reverse=True)
    if sort_mode == "Date modified (newest)":
        return sorted(results, key=lambda r: r["modified"], reverse=True)
    if sort_mode == "Date modified (oldest)":
        return sorted(results, key=lambda r: r["modified"])
    if sort_mode == "Largest first":
        return sorted(results, key=lambda r: r["size"], reverse=True)
    if sort_mode == "Smallest first":
        return sorted(results, key=lambda r: r["size"])
    if sort_mode == "Duplicates only":
        return find_duplicates(results)
    return results  # "Best match" - already sorted by score from scan_library


def _quick_fingerprint(path, chunk_size=1024 * 1024):
    """A fast, cheap stand-in for a full file hash - the file's size plus
    an MD5 of its first and last chunk (1MB each by default), rather
    than hashing potentially huge video files start to finish. Not
    cryptographically rigorous, but more than enough to correctly catch
    real duplicate downloads (byte-identical copies of the same source),
    while staying fast even across a large library. Returns None if the
    file can't be read (permissions, since removed, etc) - excluded from
    duplicate grouping rather than crashing the whole search."""
    try:
        size = os.path.getsize(path)
        h = hashlib.md5()
        with open(path, "rb") as f:
            h.update(f.read(chunk_size))
            if size > chunk_size:
                f.seek(max(0, size - chunk_size))
                h.update(f.read(chunk_size))
        return (size, h.hexdigest())
    except OSError:
        return None


def find_duplicates(results):
    """Groups the given results by quick fingerprint and returns only
    the ones that have at least one match - i.e. actual duplicate sets,
    not the whole library. Each returned item gets a 'duplicate_group'
    number so the UI can show which files match which (all items
    sharing the same group number are believed to be the same file)."""
    fingerprints = {}
    for item in results:
        fp = _quick_fingerprint(item["path"])
        if fp is None:
            continue
        fingerprints.setdefault(fp, []).append(item)

    duplicates = []
    group_id = 0
    for items in fingerprints.values():
        if len(items) > 1:
            group_id += 1
            for item in items:
                item = dict(item)  # don't mutate the caller's original dicts
                item["duplicate_group"] = group_id
                duplicates.append(item)
    duplicates.sort(key=lambda r: r["duplicate_group"])
    return duplicates


def discover_subdirectories(directory, max_depth=3, max_results=50):
    """Lists every subdirectory under `directory`, for the small
    informational preview shown under each folder in Settings > Files -
    confirming what a folder's contents actually look like before the
    user decides whether to include subfolders in the scan. Capped at a
    modest depth/count so a folder with thousands of nested directories
    doesn't hang the Settings tab while it's building this preview."""
    found = []
    if not directory or not os.path.isdir(directory):
        return found
    base_depth = directory.rstrip(os.sep).count(os.sep)
    for root, dirs, _files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        depth = root.rstrip(os.sep).count(os.sep) - base_depth
        if depth >= max_depth:
            dirs[:] = []
            continue
        for d in dirs:
            found.append(os.path.join(root, d))
            if len(found) >= max_results:
                return found
    return found
