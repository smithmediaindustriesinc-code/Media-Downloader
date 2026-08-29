import glob
import os
import queue
import threading
import time
import yt_dlp

from core.dependencies import check_ffmpeg, FFMPEG_DIR
from core.speed_tracker import SpeedTracker

VIDEO_QUALITY_MAP = {
    "Best": "bestvideo+bestaudio/best",
    "4K / 2160p": "bestvideo[height<=2160]+bestaudio/best[height<=2160]",
    "1440p": "bestvideo[height<=1440]+bestaudio/best[height<=1440]",
    "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]",
    "360p": "bestvideo[height<=360]+bestaudio/best[height<=360]",
    "240p": "bestvideo[height<=240]+bestaudio/best[height<=240]",
}
VIDEO_QUALITIES = list(VIDEO_QUALITY_MAP.keys())
VIDEO_FORMATS = ["mp4", "mkv", "webm", "mov", "avi"]
AUDIO_FORMATS = ["mp3", "wav", "m4a", "flac", "opus", "aac", "vorbis"]
AUDIO_QUALITIES = ["320", "256", "224", "192", "160", "128", "96", "64"]

ASPECT_RATIOS = {
    "Any": None,
    "16:9 (Standard)": 16 / 9,
    "9:16 (Vertical / Shorts)": 9 / 16,
    "4:3 (Classic)": 4 / 3,
    "1:1 (Square)": 1 / 1,
    "21:9 (Ultrawide)": 21 / 9,
}
ASPECT_RATIO_OPTIONS = list(ASPECT_RATIOS.keys())

HEIGHT_CAPS = {
    "Best": 999999, "4K / 2160p": 2160, "1440p": 1440, "1080p": 1080,
    "720p": 720, "480p": 480, "360p": 360, "240p": 240,
}


class DownloadCancelled(Exception):
    pass


class DownloadStageError(Exception):
    """Carries which stage of the download pipeline failed, so the GUI can
    tell the user exactly what went wrong instead of a generic error."""
    def __init__(self, stage, original):
        self.stage = stage
        self.original = original
        super().__init__(f"[{stage}] {original}")


class YouTubeBotDetectedError(Exception):
    """Raised specifically for YouTube's 'Sign in to confirm you're not a
    bot' error - distinct from DownloadStageError because retrying this
    one is pointless (it's not a transient network hiccup, it won't
    resolve itself on attempt 2 or 3) and because the GUI needs to react
    to it very differently: a plain-language popup instead of a log line,
    and stopping the whole batch/queue rather than moving on to the next
    URL and hitting the exact same wall repeatedly."""
    def __init__(self, original):
        self.original = original
        super().__init__(str(original))


class CookieAccessError(Exception):
    """Raised when yt-dlp can't read cookies from the chosen browser -
    most commonly Chrome, which locks its cookie database while running
    (see yt-dlp issue #7271). Retrying does nothing here either: the fix
    is closing the browser first or picking a different one, not another
    attempt with the exact same lock in place."""
    def __init__(self, original):
        self.original = original
        super().__init__(str(original))


_BOT_DETECTION_SIGNATURES = (
    "sign in to confirm you're not a bot",
    "sign in to confirm you\u2019re not a bot",  # curly apostrophe variant yt-dlp actually uses
)


def _is_bot_detection_error(exc):
    return any(sig in str(exc).lower() for sig in _BOT_DETECTION_SIGNATURES)


def _is_cookie_access_error(exc):
    text = str(exc).lower()
    return "could not copy" in text and "cookie database" in text


MAX_DOWNLOAD_ATTEMPTS = 3


def download_with_retry(download_fn, log_callback=None, max_attempts=MAX_DOWNLOAD_ATTEMPTS, **kwargs):
    """Calls download_fn(**kwargs) up to max_attempts times, retrying on
    any DownloadStageError (network hiccups, transient extractor errors,
    etc) but never on DownloadCancelled (a user cancelling should stop
    immediately) or YouTubeBotDetectedError (retrying won't fix a bot
    challenge - it needs cookies or a cooldown, not another attempt).
    Returns the download_fn's return value on success, or re-raises the
    last error after all attempts are used up."""
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return download_fn(**kwargs)
        except (DownloadCancelled, YouTubeBotDetectedError, CookieAccessError):
            raise
        except DownloadStageError as e:
            last_error = e
            if log_callback and attempt < max_attempts:
                log_callback(f"Attempt {attempt}/{max_attempts} failed ({e.stage}): {e.original} - retrying...")
    raise last_error


def _ffmpeg_options():
    ok, path = check_ffmpeg()
    if not ok:
        return {}
    # yt-dlp wants the DIRECTORY containing ffmpeg/ffprobe, not the exe
    # path itself. Using os.path.dirname(path) here (rather than always
    # assuming FFMPEG_DIR) is what makes this correctly follow whichever
    # of the three locations check_ffmpeg() actually found - the
    # AppData copy, one bundled with the installer, or the system PATH.
    return {"ffmpeg_location": os.path.dirname(path)}


def _cookie_options(cookies_from_browser):
    """yt-dlp's actual, documented fix for YouTube's bot challenge: pull
    cookies from an already-signed-in browser session, so requests carry
    the same identity a real logged-in browser would, instead of looking
    like an anonymous script. cookies_from_browser is one of "none",
    "chrome", "firefox", "edge", "brave", "safari", "opera", "vivaldi" -
    matching yt-dlp's own supported browser names exactly."""
    if not cookies_from_browser or cookies_from_browser == "none":
        return {}
    return {"cookiesfrombrowser": (cookies_from_browser,)}


def fetch_media_info(url):
    """Full yt-dlp metadata lookup for a single media URL (no download) -
    returns the raw, flattened info dict: title, thumbnail, the complete
    format list, everything yt-dlp extracts. fetch_info() is a thin
    wrapper over this for callers that only want title/thumbnail; callers
    that also need the format list (aspect-ratio selection) can pass this
    same dict back in as `prefetched_info` instead of paying for a second
    identical extraction. Raises DownloadStageError('info fetch', ...) on
    failure."""
    options = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    options.update(_ffmpeg_options())
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get("entries"):
                info = info["entries"][0]
            return info
    except Exception as e:
        raise DownloadStageError("info fetch", e)


def fetch_info(url):
    """Look up title + thumbnail URL without downloading. Raises
    DownloadStageError('info fetch', ...) on failure so the caller can show
    a specific message."""
    info = fetch_media_info(url)
    return {
        "title": info.get("title", "download"),
        "thumbnail": info.get("thumbnail"),
    }


def fetch_title(url):
    return fetch_info(url)["title"]


class PlaylistFetchTimeout(Exception):
    """Raised when a playlist lookup makes no progress within the per-window
    timeout - genuinely hung, not just large. See fetch_playlist_info."""
    pass


class PlaylistFetchCancelled(Exception):
    """Raised when the caller's cancel_event is set during a playlist lookup
    (e.g. the Cancel button) - distinct from a timeout so the GUI doesn't
    show a scary 'timed out' error for a deliberate cancel."""
    pass


_PLAYLIST_WINDOW = 300        # entries fetched per bounded extract_info call
_PLAYLIST_MAX_WINDOWS = 2000  # sanity cap (~600k entries) against a runaway paginator


def _coerce_timeout(value, default=60):
    try:
        return max(15, int(value))
    except (TypeError, ValueError):
        return default


def fetch_playlist_info(url, timeout_seconds=60, cancel_event=None):
    """Look up a playlist's own title plus its entries' titles/URLs, without
    downloading anything. Used to name the auto-created playlist when
    'Download playlist' is on and to get the list of entry URLs to fetch.

    Enumerates the playlist in BOUNDED WINDOWS (_PLAYLIST_WINDOW entries per
    yt-dlp call via `playlist_items`), in a worker thread that pushes each
    entry onto a queue as its window resolves. `timeout_seconds` is applied
    per window (no new progress for that long -> treated as hung), not as a
    total budget, so a playlist with tens of thousands of entries that keeps
    making progress runs to completion. If some entries were collected and
    then it stalls, that partial list is returned rather than failing.

    cancel_event: optional threading.Event; when set, enumeration stops
    within ~1s and PlaylistFetchCancelled is raised.

    Raises: PlaylistFetchCancelled on cancel; PlaylistFetchTimeout if a
    window makes no progress in time and nothing was collected;
    DownloadStageError('playlist info fetch', ...) on a hard extractor
    failure with no partial results."""
    window_timeout = _coerce_timeout(timeout_seconds)
    result_queue = queue.Queue()
    internal_stop = threading.Event()  # main thread -> worker: stop now

    def cancelled():
        return internal_stop.is_set() or (cancel_event is not None and cancel_event.is_set())

    def worker():
        base = {"quiet": True, "no_warnings": True, "skip_download": True,
                "extract_flat": "in_playlist", "noplaylist": False,
                "ignoreerrors": True,
                "socket_timeout": max(15, min(window_timeout, 60))}
        base.update(_ffmpeg_options())
        try:
            start, sent_meta = 1, False
            for _ in range(_PLAYLIST_MAX_WINDOWS):
                if cancelled():
                    break
                opts = dict(base, playlist_items=f"{start}:{start + _PLAYLIST_WINDOW - 1}")
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info is None:
                        if not sent_meta:
                            result_queue.put(("error", ValueError(
                                "This URL didn't return any playlist data.")))
                            return
                        break
                    if not sent_meta:
                        result_queue.put(("meta", {k: info.get(k)
                                                   for k in ("title", "id", "webpage_url")}))
                        sent_meta = True
                    window = [e for e in (info.get("entries") or []) if e]
                for entry in window:
                    if cancelled():
                        break
                    result_queue.put(("entry", entry))
                if len(window) < _PLAYLIST_WINDOW or cancelled():
                    break
                start += _PLAYLIST_WINDOW
            result_queue.put(("done", None))
        except Exception as e:
            result_queue.put(("error", e))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    meta, collected = {}, []
    last_progress = time.monotonic()
    while True:
        if cancel_event is not None and cancel_event.is_set():
            internal_stop.set()
            raise PlaylistFetchCancelled("Playlist lookup was cancelled.")
        try:
            kind, payload = result_queue.get(timeout=1.0)
        except queue.Empty:
            if time.monotonic() - last_progress > window_timeout:
                internal_stop.set()  # abandon the worker (can't force-kill a thread)
                if not collected and not meta:
                    raise PlaylistFetchTimeout(
                        f"This playlist made no progress for {window_timeout}s and was treated "
                        f"as hung. Raise the timeout in Settings > Advanced if it's just very slow.")
                break  # partial results
            continue
        last_progress = time.monotonic()
        if kind == "meta":
            meta = payload or {}
        elif kind == "entry":
            collected.append(payload)
        elif kind == "done":
            break
        elif kind == "error":
            if not collected:
                raise DownloadStageError("playlist info fetch", payload)
            break  # partial results are still useful

    if cancel_event is not None and cancel_event.is_set():
        raise PlaylistFetchCancelled("Playlist lookup was cancelled.")

    entry_list = [{"title": (e.get("title") or "Untitled"),
                   "url": (e.get("url") or e.get("webpage_url") or url)}
                  for e in collected if e]
    playlist_title = meta.get("title")
    if not playlist_title and entry_list:
        playlist_title = entry_list[0]["title"]
    if not playlist_title:
        playlist_title = "Playlist"
    return {"playlist_title": playlist_title, "entries": entry_list}


def cleanup_partial_files(out_dir, name, max_attempts=6, retry_delay=0.2):
    """Deletes every file that a SINGLE in-progress download attempt for
    'name' may have created (the final file, .part fragments, temp
    thumbnail/subtitle files yt-dlp leaves around, etc) - used when a
    download is cancelled partway through.

    A cancel doesn't instantly release the file: the DownloadCancelled
    exception has to unwind back up through yt-dlp's own internals
    before it actually closes whatever file handle it was writing to,
    and on Windows specifically (unlike POSIX) a file can't be deleted
    at all while something still has it open - so a single immediate
    delete attempt right after cancelling can genuinely fail with a
    "file in use" OSError even though the file really is abandoned and
    safe to remove a moment later. Retrying with a short delay between
    attempts is what actually fixes this, rather than trying once and
    silently giving up.

    This is safe to call after a queue/playlist item is cancelled without
    touching anything else in the same batch: 'name' is the exact,
    unique-per-item filename base yt-dlp was told to use for THIS item
    only (see make_unique_name in core/utils.py), so the glob pattern
    below can only ever match files this specific item created - it
    cannot accidentally delete a sibling item's already-completed file,
    even sitting in the very same output folder.

    Returns (removed, still_locked) - two lists of paths, so a caller can
    tell the user if something genuinely couldn't be cleaned up (still
    locked by another process, e.g. antivirus scanning it) rather than
    that being silent."""
    if not out_dir or not name:
        return [], []
    pattern = os.path.join(out_dir, f"{name}.*")
    removed = []
    still_locked = []
    for path in glob.glob(pattern):
        deleted = False
        for attempt in range(max_attempts):
            try:
                os.remove(path)
                removed.append(path)
                deleted = True
                break
            except OSError:
                if attempt < max_attempts - 1:
                    time.sleep(retry_delay)
        if not deleted:
            still_locked.append(path)
    return removed, still_locked


def _format_for_aspect_ratio(url, quality_key, aspect_key, info=None):
    """Pick an explicit format id whose width/height ratio best matches the
    requested aspect ratio, capped by the requested quality. If `info` (a
    yt-dlp info dict already fetched by the caller) is given, its format
    list is reused rather than running a second, identical extraction just
    for this."""
    target = ASPECT_RATIOS.get(aspect_key)
    if not target:
        return VIDEO_QUALITY_MAP.get(quality_key, VIDEO_QUALITY_MAP["Best"])

    if info is not None:
        formats = info.get("formats", [])
    else:
        options = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
        options.update(_ffmpeg_options())
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get("entries"):
                info = info["entries"][0]
            formats = info.get("formats", [])

    candidates = [f for f in formats if f.get("vcodec") not in (None, "none")
                  and f.get("width") and f.get("height")]
    if not candidates:
        return VIDEO_QUALITY_MAP.get(quality_key, VIDEO_QUALITY_MAP["Best"])

    height_cap = HEIGHT_CAPS.get(quality_key, 999999)

    def ratio_diff(f):
        return abs((f["width"] / f["height"]) - target)

    capped = [f for f in candidates if (f.get("height") or 0) <= height_cap] or candidates
    capped.sort(key=lambda f: (round(ratio_diff(f), 2), -(f.get("height") or 0)))
    chosen = capped[0]
    return f"{chosen['format_id']}+bestaudio/best"


def _estimate_bytes_from_info(info):
    """Given a PROCESSED yt-dlp info dict (format selection already applied,
    so it carries requested_downloads / requested_formats), return the best
    available byte estimate for what would actually be written to disk -
    summing separate video+audio streams when the chosen format is a merge.
    Returns an int, or None when nothing usable is present."""
    if not info:
        return None

    def _one(f):
        f = f or {}
        return f.get("filesize") or f.get("filesize_approx")

    downloads = info.get("requested_downloads")
    if downloads:
        total, seen = 0, False
        for d in downloads:
            subs = d.get("requested_formats")
            if subs:
                for s in subs:
                    v = _one(s)
                    if v:
                        total += v
                        seen = True
            else:
                v = _one(d)
                if v:
                    total += v
                    seen = True
        if seen:
            return int(total)

    reqf = info.get("requested_formats")
    if reqf:
        total, seen = 0, False
        for s in reqf:
            v = _one(s)
            if v:
                total += v
                seen = True
        if seen:
            return int(total)

    v = _one(info)
    return int(v) if v else None


def fetch_download_size(url, dtype="Video", quality_key="Best", fmt="mp4",
                        aspect_ratio="Any", cookies_from_browser="none"):
    """Best-effort estimate, in bytes, of what a REAL download of `url` at the
    current settings would pull down - used by the optional batch "pre-fetch
    file sizes" stage so the queue/item ETAs can be size-based rather than
    item-count-based.

    Uses the SAME format selector the real download would use (bestaudio/best
    for audio; _format_for_aspect_ratio()'s pick for video, so an explicit
    aspect ratio is honoured), runs yt-dlp with skip_download/simulate and
    nothing written, and reads the resolved format's filesize /
    filesize_approx (summing video+audio when they're separate streams).

    Fast-ish (one extraction, or two only when a non-"Any" aspect ratio
    forces a format-list lookup) and NEVER raises - returns None on any
    failure or when the size genuinely isn't known."""
    try:
        options = {
            "quiet": True, "no_warnings": True, "skip_download": True,
            "simulate": True, "noplaylist": True, "noprogress": True,
        }
        options.update(_ffmpeg_options())
        options.update(_cookie_options(cookies_from_browser))

        if dtype == "Audio":
            fmt_selector = "bestaudio/best"
        else:
            try:
                fmt_selector = _format_for_aspect_ratio(url, quality_key, aspect_ratio)
            except Exception:
                fmt_selector = VIDEO_QUALITY_MAP.get(quality_key, VIDEO_QUALITY_MAP["Best"])
        options["format"] = fmt_selector

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        if info and info.get("entries"):
            entries = [e for e in info["entries"] if e]
            info = entries[0] if entries else None
        return _estimate_bytes_from_info(info)
    except Exception:
        return None


def _cleanup_stray_part_files(out_dir, name, final_path):
    """After a SUCCESSFUL download, removes any leftover .part/.ytdl
    fragment files - a real, confirmed gap in yt-dlp's own cleanup,
    especially with concurrent_fragment_downloads (used here) and
    post-processing (merge/recode/extract) leaving intermediate files
    behind. Never touches final_path itself, only genuine stray
    leftovers matching this item's own name."""
    for pattern in (f"{name}.*.part", f"{name}.part", f"{name}.*.ytdl", f"{name}.ytdl"):
        for path in glob.glob(os.path.join(out_dir, pattern)):
            if os.path.abspath(path) == os.path.abspath(final_path):
                continue
            try:
                os.remove(path)
            except OSError:
                pass  # a stray file that can't be removed isn't worth failing a successful download over


class Downloader:
    """Wraps yt-dlp for video/audio downloads with progress + cancel support
    and stage-aware error reporting."""

    def __init__(self, progress_callback=None, log_callback=None, ping_ms_provider=None):
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self._cancel = False
        self.item_start_time = None    # set when a single URL's download begins
        self.last_speed = None         # bytes/sec, from the most recent hook call (raw, unsmoothed)
        self.last_eta_seconds = None
        self.last_total_bytes = None       # current item's total size (bytes), from the most recent hook call
        self.last_downloaded_bytes = 0     # current item's bytes fetched so far, from the most recent hook call
        # ping_ms_provider: an optional zero-arg callable returning the
        # app's most recently measured connection latency (see
        # core/network_status.py) - used to scale the speed tracker's
        # outlier tolerance. None if not provided (the tracker falls
        # back to a moderate default tolerance in that case).
        self.ping_ms_provider = ping_ms_provider
        self.speed_tracker = SpeedTracker()

    def cancel(self):
        self._cancel = True

    def _log(self, msg):
        if self.log_callback:
            self.log_callback(msg)

    def elapsed_seconds(self):
        if self.item_start_time is None:
            return 0.0
        return time.time() - self.item_start_time

    def _hook(self, d):
        if self._cancel:
            raise DownloadCancelled("Download cancelled by user.")
        if d.get("status") == "downloading":
            speed = d.get("speed")
            self.last_speed = speed
            ping = self.ping_ms_provider() if self.ping_ms_provider else None
            self.speed_tracker.add_sample(speed, ping_ms=ping)
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            self.last_total_bytes = total
            self.last_downloaded_bytes = downloaded
            pct = (downloaded / total) if total else 0
            # ETA from the SMOOTHED 5-second-average speed, not the raw
            # instant reading - a single jittery hook call shouldn't
            # make the estimate swing wildly from one moment to the
            # next, per how this was specifically asked for. Falls back
            # to the raw speed (then yt-dlp's own 'eta' field) if there
            # aren't enough samples averaged yet for a smoothed value.
            smoothed_speed = self.speed_tracker.get_average(window_seconds=5)
            eta_speed = smoothed_speed or speed
            if eta_speed and total:
                remaining = max(total - downloaded, 0)
                self.last_eta_seconds = remaining / eta_speed
            else:
                self.last_eta_seconds = d.get("eta")
            if self.progress_callback:
                self.progress_callback(pct, speed)
        elif d.get("status") == "finished":
            self._log("Download finished, post-processing / merging...")

    def download_video(self, url, name, out_dir, quality_key, fmt,
                        playlist=False, subtitles=False, aspect_ratio="Any", cookies_from_browser="none",
                        prefetched_info=None):
        self._cancel = False
        self.item_start_time = time.time()
        self.last_speed = None
        self.speed_tracker.reset()
        self.last_eta_seconds = None
        self.last_total_bytes = None
        self.last_downloaded_bytes = 0
        os_safe_dir = out_dir.rstrip("/\\")
        outtmpl = f"{os_safe_dir}/{name}.%(ext)s"

        try:
            fmt_selector = _format_for_aspect_ratio(url, quality_key, aspect_ratio, info=prefetched_info)
        except Exception as e:
            raise DownloadStageError("format selection", e)

        options = {
            "format": fmt_selector,
            "outtmpl": outtmpl,
            "merge_output_format": fmt,
            "recode_video": fmt,
            "progress_hooks": [self._hook],
            "noplaylist": not playlist,
            "quiet": True,
            "no_warnings": True,
            # Fetches multiple fragments of a DASH/HLS stream in parallel
            # instead of one at a time - often a large, easy speed win on
            # sites that serve video in chunks (which is most of them),
            # bounded so it doesn't hammer a slow connection or a strict
            # server into throttling/blocking.
            "concurrent_fragment_downloads": 4,
        }
        options.update(_ffmpeg_options())
        options.update(_cookie_options(cookies_from_browser))
        if subtitles:
            options["writesubtitles"] = True
            options["writeautomaticsub"] = True
            options["subtitleslangs"] = ["en"]
            options["subtitlesformat"] = "srt"
            options["embedsubtitles"] = False

        self._log(f"Analyzing link: {url}")
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([url])
        except DownloadCancelled:
            raise
        except Exception as e:
            if _is_bot_detection_error(e):
                raise YouTubeBotDetectedError(e)
            if _is_cookie_access_error(e):
                raise CookieAccessError(e)
            raise DownloadStageError("download/merge", e)

        final_path = f"{os_safe_dir}/{name}.{fmt}"
        if not os.path.exists(final_path):
            raise DownloadStageError(
                "verification",
                f"yt-dlp reported success but the output file wasn't found at {final_path}. "
                f"This usually means merging/recoding failed silently - check that FFmpeg is installed correctly."
            )
        self._log(f"Saved to {final_path}")
        _cleanup_stray_part_files(os_safe_dir, name, final_path)
        return final_path

    def download_audio(self, url, name, out_dir, quality, fmt,
                        playlist=False, embed_thumbnail=True, cookies_from_browser="none"):
        self._cancel = False
        self.item_start_time = time.time()
        self.last_speed = None
        self.speed_tracker.reset()
        self.last_eta_seconds = None
        self.last_total_bytes = None
        self.last_downloaded_bytes = 0
        os_safe_dir = out_dir.rstrip("/\\")
        outtmpl = f"{os_safe_dir}/{name}.%(ext)s"
        postprocessors = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": fmt,
            "preferredquality": quality,
        }]
        options = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "postprocessors": postprocessors,
            "progress_hooks": [self._hook],
            "noplaylist": not playlist,
            "quiet": True,
            "no_warnings": True,
            "concurrent_fragment_downloads": 4,
        }
        options.update(_ffmpeg_options())
        options.update(_cookie_options(cookies_from_browser))
        if embed_thumbnail:
            options["writethumbnail"] = True
            postprocessors.append({"key": "FFmpegMetadata"})
            if fmt in ("mp3", "m4a", "flac", "opus"):
                postprocessors.append({"key": "EmbedThumbnail"})

        self._log(f"Analyzing link: {url}")
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([url])
        except DownloadCancelled:
            raise
        except Exception as e:
            if _is_bot_detection_error(e):
                raise YouTubeBotDetectedError(e)
            if _is_cookie_access_error(e):
                raise CookieAccessError(e)
            raise DownloadStageError("download/extract", e)

        final_path = f"{os_safe_dir}/{name}.{fmt}"
        if not os.path.exists(final_path):
            raise DownloadStageError(
                "verification",
                f"yt-dlp reported success but the output file wasn't found at {final_path}. "
                f"This usually means audio extraction failed silently - check that FFmpeg is installed correctly."
            )
        self._log(f"Saved to {final_path}")
        _cleanup_stray_part_files(os_safe_dir, name, final_path)
        return final_path
