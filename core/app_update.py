"""
In-app update check for Media Downloader itself.

Reads the version manifest published by the release process to the PUBLIC
distribution repo (no auth needed - the private source repo is untouched):
    https://github.com/smithmediaindustriesinc-code/Media-Downloader-Releases
        versions.json  ->  [ {version, channel, date, asset, url, notes}, ... ]  newest first
        channel: "stable" (a normal release) | "beta" (a prerelease/preview)

check_app_update() compares the running APP_VERSION against the newest entry
in the selected channel and, when the app is a frozen/installed build, hands
back the installer URL so the Version tab can download + run it. Running from
source there is nothing to install - the check just reports the newest
version and says to update via git.

Nothing here raises: a missing network, a malformed manifest, etc. all come
back as "couldn't check" rather than an exception into the GUI thread.
"""
import json
import os
import re
import sys
import tempfile
import urllib.request

MANIFEST_URL = ("https://raw.githubusercontent.com/smithmediaindustriesinc-code/"
                "Media-Downloader-Releases/main/versions.json")

_UA = "MediaDownloader-app-update-check"


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def _version_key(v):
    """Sort key for a version string. '1.6.10' -> (1, 6, 10, 0);
    a '-preview'/'-beta'/'-rc' suffix sorts BELOW the same numbers with no
    suffix, so 1.5.4-preview < 1.5.4. Unparseable -> sorts lowest."""
    if not v:
        return (0,)
    s = str(v).strip().lstrip("vV")
    m = re.match(r"(\d+(?:\.\d+)*)(.*)$", s)
    if not m:
        return (0,)
    nums = tuple(int(x) for x in m.group(1).split("."))
    # pad to 3 so (1,6) and (1,6,0) compare equal-ish
    nums = nums + (0,) * (3 - len(nums)) if len(nums) < 3 else nums
    pre = 0 if not m.group(2).strip() else -1
    return nums + (pre,)


def fetch_manifest(timeout=10):
    """The version list, newest-first, or None on any failure."""
    try:
        req = urllib.request.Request(MANIFEST_URL, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, list) and data:
            return data
    except Exception:
        pass
    return None


def _newest_in_channel(manifest, include_beta):
    best = None
    for entry in manifest:
        ch = (entry.get("channel") or "stable").lower()
        if ch != "stable" and not include_beta:
            continue
        if best is None or _version_key(entry.get("version")) > _version_key(best.get("version")):
            best = entry
    return best


def check_app_update(current_version, include_beta=False, timeout=10):
    """Returns a dict:
        ok            - True when nothing to do (up to date, or couldn't check)
        checked       - True if the manifest was actually reached
        current       - the running version string
        latest        - newest version in the selected channel (or None)
        url           - installer download URL for `latest` (frozen builds only)
        latest_url     - installer URL for `latest` regardless of whether it's
                         newer (frozen builds only); used for "get a separate copy"
        update_available - True when latest is newer than current AND we can install it
        detail        - one-line human summary for the Version tab row
    """
    result = {
        "ok": True, "checked": False, "current": current_version,
        "latest": None, "url": None, "latest_url": None, "update_available": False,
        "detail": "Couldn't check for updates (no connection?).",
    }
    manifest = fetch_manifest(timeout=timeout)
    if manifest is None:
        return result
    result["checked"] = True

    newest = _newest_in_channel(manifest, include_beta)
    if newest is None:
        result["detail"] = "No matching release found in the version list."
        return result

    latest = newest.get("version")
    result["latest"] = latest
    if is_frozen():
        result["latest_url"] = newest.get("url")
    cur_k, lat_k = _version_key(current_version), _version_key(latest)

    if lat_k <= cur_k:
        chan = "beta" if include_beta else "stable"
        result["detail"] = f"Up to date (running {current_version}, newest {chan} is {latest})."
        return result

    # A newer version exists.
    if not is_frozen():
        result["detail"] = (f"Version {latest} is available. You're running from source - "
                             f"update with 'git pull' in the repo.")
        return result

    result["ok"] = False
    result["update_available"] = True
    result["url"] = newest.get("url")
    notes = (newest.get("notes") or "").strip()
    result["detail"] = f"Update available: {latest}" + (f" - {notes}" if notes else "")
    return result


def download_installer(url, progress_callback=None, timeout=60):
    """Download the installer to a temp file. Returns (path, None) on success
    or (None, error_message). Never raises."""
    if not url:
        return None, "No download URL for this update."
    try:
        fname = os.path.basename(url.split("?")[0]) or "MediaDownloaderSetup.exe"
        dest = os.path.join(tempfile.gettempdir(), fname)
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if progress_callback and total:
                        progress_callback(got, total)
        if os.path.getsize(dest) < 1024:
            return None, "The downloaded installer looks empty."
        return dest, None
    except Exception as e:
        return None, f"Download failed: {e}"
