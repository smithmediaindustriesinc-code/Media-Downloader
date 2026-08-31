"""
Scrapes an arbitrary web page for the real, watchable videos embedded on it
(the ones a person would actually sit and watch), using yt-dlp's *generic*
extractor.

Why yt-dlp instead of a headless browser (this replaced a Playwright/Chromium
network-sniffer in 1.7.3): yt-dlp's generic extractor is purpose-built for
"given a page URL, find the content video". It detects <video> tags and their
<source>s, Open Graph `og:video`, Twitter-card video, JSON-LD `VideoObject`,
HLS/DASH manifests, and - crucially - <iframe> embeds, which it hands off to
the ~1800 site-specific extractors (YouTube, Vimeo, Dailymotion, Twitch, ...).
It deliberately ignores the junk a raw network sniff trips on: tracking
pixels, ad creatives, loose HLS segments, preview blips. A page with several
embeds comes back as several entries.

One known limitation: a handful of sites only inject their <video> via
JavaScript after a scroll/interaction (some infinite-scroll galleries, logged-
in social timelines). yt-dlp has dedicated extractors for the big ones; for a
truly obscure JS-only page this may find nothing.
"""
import re

VIDEO_EXTENSIONS_RE = re.compile(r"\.(mp4|webm|mov|avi|mkv|flv|wmv|m3u8|mpd)(\?|$)", re.IGNORECASE)
AUDIO_EXTENSIONS_RE = re.compile(r"\.(mp3|wav|m4a|ogg|flac|aac|opus)(\?|$)", re.IGNORECASE)


def _classify(url, content_type=None):
    """Video/audio classification by content-type first (when available),
    falling back to the URL's own file extension. Returns "video", "audio",
    or None. Kept for the Download-tab routing that decides Video vs Audio
    mode for queued scrape results."""
    if content_type:
        ct = content_type.lower()
        if ct.startswith("video/"):
            return "video"
        if ct.startswith("audio/"):
            return "audio"
    if VIDEO_EXTENSIONS_RE.search(url):
        return "video"
    if AUDIO_EXTENSIONS_RE.search(url):
        return "audio"
    return None


def _entry_to_item(e):
    if not isinstance(e, dict):
        return None
    url = (e.get("webpage_url") or e.get("url") or e.get("original_url")
           or e.get("id"))
    if not url or not isinstance(url, str):
        return None
    url = url.split("#__youtubedl_smuggle=")[0]  # strip yt-dlp's internal fragment
    name = e.get("title") or e.get("alt_title") or e.get("id") or url
    if e.get("vcodec") == "none" or e.get("acodec") and e.get("vcodec") is None:
        kind = "audio"
    else:
        kind = _classify(url) or "video"
    duration = e.get("duration")
    return {"url": url, "name": str(name), "kind": kind,
            "duration": duration if isinstance(duration, (int, float)) else None}


def scrape_media_urls(page_url, media_type="both", log_callback=None, timeout_ms=None):
    """Return every watchable video/audio embedded on `page_url` as a list of
    dicts: {"url", "name", "kind", "duration"}, in first-seen order, de-duped.
    `media_type` filters to "video" / "audio" / "both". Never raises for a page
    that partly fails - returns whatever was extracted.

    Unlike the old two-stage flow, titles come back in this single pass, so
    there is no separate fetch_titles_for_urls() step to run afterwards.
    """
    import yt_dlp

    def log(msg):
        if log_callback:
            log_callback(msg)

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": False,          # a page with several embeds -> collect them all
        "ignoreerrors": True,
        "playlist_items": "1-50",     # sanity cap for a page that resolves to a huge feed
    }

    log(f"Scanning {page_url} ...")
    info = None
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(page_url, download=False)
    except Exception as e:
        log(f"Could not read that page: {e}")
        return []

    if not info:
        log("Nothing watchable found on that page.")
        return []

    raw = []
    if info.get("_type") == "playlist" or info.get("entries") is not None:
        for e in (info.get("entries") or []):
            it = _entry_to_item(e)
            if it:
                raw.append(it)
    else:
        it = _entry_to_item(info)
        if it:
            raw.append(it)

    seen, out = set(), []
    for it in raw:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        if media_type != "both" and it["kind"] != media_type:
            continue
        out.append(it)

    log(f"Found {len(out)} video(s).")
    return out


def fetch_titles_for_urls(urls, log_callback=None):
    """Back-compat shim. scrape_media_urls() now returns titles directly, so
    this just normalises whatever it's handed into (url, title) pairs."""
    out = []
    for u in urls:
        if isinstance(u, dict):
            out.append((u.get("url"), u.get("name") or u.get("url")))
        else:
            out.append((u, u))
    return out


# --- Back-compat stubs: the page scraper no longer needs a browser component.
# installer.iss's [Run] step and main.py's --playwright-install flag are gone,
# but keep these importable so anything still referencing them is harmless.
def ensure_playwright_browser_installed():
    return True, "The page scraper no longer needs a separate browser component."


def _chromium_available():
    return True, "not required"
