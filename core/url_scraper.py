"""
Scrapes a page for video/audio media URLs using Playwright (a real
headless browser, not a plain HTML parse - needed because a lot of
sites load media dynamically via JavaScript, HLS/DASH streaming
manifests, or content that only ever appears as a network response,
never as a static <video src="..."> in the original HTML). Once URLs
are found, fetching their real titles via yt-dlp is a SEPARATE step
(fetch_titles_for_urls) - matching exactly how this was asked for:
scrape first, fetch names second, only THEN show anything to the user.
"""
import re
import subprocess
import sys
from urllib.parse import urljoin

VIDEO_EXTENSIONS_RE = re.compile(r"\.(mp4|webm|mov|avi|mkv|flv|wmv|m3u8|mpd)(\?|$)", re.IGNORECASE)
AUDIO_EXTENSIONS_RE = re.compile(r"\.(mp3|wav|m4a|ogg|flac|aac|opus)(\?|$)", re.IGNORECASE)


def _classify(url, content_type=None):
    """Video/audio classification by content-type first (most reliable,
    when a network response actually has one), falling back to the
    URL's own file extension otherwise. Returns "video", "audio", or
    None (not a media URL at all)."""
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


def scrape_media_urls(page_url, media_type="both", timeout_ms=20000, log_callback=None):
    """Loads page_url in a real headless Chromium and collects every
    media URL it can find, from two independent sources: (1) every
    <video>/<audio>/<source> element's actual current src (read from the
    LIVE DOM after JS has run, not the original HTML - catches src
    attributes set dynamically after page load), and (2) every network
    response whose content-type is video/* or audio/* (catches
    HLS/DASH streaming and JS-fetched media that never appears as a
    static tag at all). media_type filters the combined results to
    "video", "audio", or "both".

    Returns a list of unique URLs (strings), in first-seen order.
    Never raises for a page that doesn't fully settle (ads, long-
    polling, etc) - whatever was captured up to that point is still
    returned rather than losing everything."""
    from playwright.sync_api import sync_playwright

    def log(msg):
        if log_callback:
            log_callback(msg)

    found = {}  # url -> "video"/"audio", insertion-ordered (dict, py3.7+)

    def on_response(response):
        try:
            content_type = response.headers.get("content-type", "")
        except Exception:
            content_type = ""
        kind = _classify(response.url, content_type)
        if kind and response.url not in found:
            found[response.url] = kind

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.on("response", on_response)
            log(f"Loading {page_url}...")
            try:
                page.goto(page_url, timeout=timeout_ms, wait_until="networkidle")
            except Exception as e:
                log(f"Page didn't fully settle ({e}) - using what was captured so far.")

            try:
                tag_urls = page.eval_on_selector_all(
                    "video, audio, source",
                    "els => els.map(el => el.currentSrc || el.src).filter(Boolean)"
                )
            except Exception:
                tag_urls = []
            for u in tag_urls:
                full = urljoin(page_url, u)
                kind = _classify(full)
                if kind and full not in found:
                    found[full] = kind
        finally:
            browser.close()

    if media_type == "video":
        results = [u for u, k in found.items() if k == "video"]
    elif media_type == "audio":
        results = [u for u, k in found.items() if k == "audio"]
    else:
        results = list(found.keys())

    log(f"Found {len(results)} media URL(s).")
    return results


def fetch_titles_for_urls(urls, log_callback=None):
    """For each scraped URL, uses yt-dlp (the app's own existing
    extraction engine, not a separate mechanism) to look up its real
    title - the second stage of "scrape first, fetch names second, only
    then show the user anything". Returns a list of (url, title) tuples
    in the same order as `urls`. A URL yt-dlp can't extract info for is
    still included, just falling back to the URL itself as its "title"
    so nothing found during scraping silently disappears from the
    results the user sees."""
    from core.downloader import fetch_info

    def log(msg):
        if log_callback:
            log_callback(msg)

    results = []
    for url in urls:
        try:
            info = fetch_info(url)
            title = info.get("title") or url
        except Exception:
            title = url
        results.append((url, title))
        log(f"Fetched name for {url}: {title}")
    return results


def ensure_playwright_browser_installed():
    """Actually downloads the Chromium browser Playwright needs to
    function - `pip install playwright` (or bundling the playwright
    Python package into a frozen build) only gets the PACKAGE; the
    browser itself is a separate multi-hundred-MB download that
    Playwright's own installer (`playwright install chromium`) fetches
    on its own. Without this step having ever run, scrape_media_urls()
    fails immediately with an "executable doesn't exist" error -
    Playwright can't find any installed browser to launch. Run
    automatically once by installer.iss's [Run] section right after
    installation (see main.py's --playwright-install flag) - also safe
    to call manually any time (e.g. after a manual pip install from
    source, or via a "Reinstall" button) since Playwright's own
    installer is idempotent and skips anything already downloaded.
    Returns (ok, message).

    Calls Playwright's own public playwright.__main__.main() entry
    point directly, IN-PROCESS - not by spawning
    `sys.executable -m playwright install chromium` as a subprocess.
    That subprocess approach was the actual, confirmed bug behind
    "the executable is not found": it works fine when running from
    source (sys.executable is a real python.exe that understands -m),
    but in the PACKAGED app, sys.executable is the app's own compiled
    .exe, which doesn't understand -m module-invocation syntax at all -
    it just receives ["-m", "playwright", "install", "chromium"] as
    plain arguments that don't match any of this app's own recognized
    flags, so it silently launched a second copy of the whole GUI app
    and did nothing useful. The browser was NEVER actually being
    downloaded for anyone using the real packaged app. Calling
    Playwright's install logic as a normal in-process function call
    instead sidesteps this entirely - it works identically regardless
    of whether the calling process is frozen or not, since it never
    depends on -m/sys.executable semantics."""
    import sys as _sys
    from playwright.__main__ import main as playwright_main

    original_argv = _sys.argv
    try:
        _sys.argv = ["playwright", "install", "chromium"]
        try:
            playwright_main()
        except SystemExit as e:
            # playwright_main() calls sys.exit() on completion (success
            # AND failure both raise SystemExit - that's just how its
            # own CLI entry point signals its result code, not
            # necessarily an error).
            if e.code not in (None, 0):
                return False, f"playwright install exited with code {e.code}."
        return True, "Playwright's Chromium browser is installed and ready for URL Scraping."
    except Exception as e:
        return False, f"Could not run the Playwright install step: {e}"
    finally:
        _sys.argv = original_argv
