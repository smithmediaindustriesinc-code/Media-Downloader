"""Parsing helper for the 1.6.1 "drag a video thumbnail onto the window"
feature (see gui/app.py's _on_thumbnail_drop / _wire_drag_and_drop).

When something is dragged from a browser onto the app, tkdnd hands us a
raw string in the drop event's ``.data``. Depending on the browser, the
source element, and which clipboard/DnD format won, that string can be:

  * a plain page URL                 "https://www.youtube.com/watch?v=ID"
  * brace-wrapped (tkdnd does this    "{https://www.youtube.com/watch?v=ID}"
    whenever an item contains a space)
  * several space/newline separated   "{http://a/x} {http://b/y}"
    items
  * an HTML fragment                  '<a href="...watch?v=ID"><img src="...thumb.jpg"></a>'
  * a bare thumbnail image URL        "https://i.ytimg.com/vi/ID/hqdefault.jpg"
  * a text/uri-list payload           "https://site/page\r\n# comment line"

``extract_video_url(raw)`` turns any of those into the best single
video/page URL it can find, or ``None`` only when there is no URL at all.
It never raises and never blocks - anything it is unsure about is passed
through as-is so the normal fetch/download step surfaces the real error.

Run this file directly (``py -3 core/drag_drop.py``) to execute the
self-tests.
"""
import re

# YouTube video ids are always exactly 11 chars from this alphabet. The
# right-boundary lookahead stops a 12+-char slug being truncated to a
# *different, valid* 11-char id (which, with auto-start, downloads the
# wrong video).
_YT_ID = r"[A-Za-z0-9_-]{11}(?![A-Za-z0-9_-])"

# File extensions that mean "this is a static image/asset, not a video page".
_IMG_EXT_RE = re.compile(
    r"\.(?:jpe?g|png|gif|webp|bmp|svg|ico|avif|tiff?)(?:[?#].*)?$", re.IGNORECASE
)

_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

_CLOSERS = {")": "(", "]": "[", "}": "{"}


def _is_http(s):
    return bool(s) and s.lower().startswith(("http://", "https://"))


def _clean(url):
    if not url:
        return url
    url = url.strip().strip("<>").strip("\"'")
    # Trim trailing sentence punctuation a URL picks up when dragged out of
    # prose - but conservatively: one pass of quote/comma/period/semicolon,
    # then close brackets only while they're genuinely unbalanced, so
    # ".../Heat_(1995_film)" keeps its parenthesis.
    url = url.rstrip("\"'.,;")
    while url and url[-1] in _CLOSERS:
        if url.count(url[-1]) > url.count(_CLOSERS[url[-1]]):
            url = url[:-1]
        else:
            break
    return url


def _looks_like_html(s):
    low = s.lower()
    return ("<a " in low or "<img " in low or "href=" in low or "src=" in low
            or "&lt;a " in low)


def _urls_from_html(s):
    """href="..." values first (the link target), then src="..." (images)."""
    out = []
    for pat in (r"href\s*=\s*[\"']([^\"']+)[\"']",
                r"src\s*=\s*[\"']([^\"']+)[\"']"):
        for m in re.finditer(pat, s, re.IGNORECASE):
            val = m.group(1).strip()
            # Un-escape the few HTML entities that show up in dragged markup.
            val = (val.replace("&amp;", "&").replace("&quot;", '"')
                      .replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">"))
            if val:
                out.append(val)
    return out


def _split_candidates(raw):
    """Break a tkdnd payload into individual candidate tokens, honoring the
    ``{...}`` wrapping tkdnd applies to any item containing whitespace."""
    cands = []
    for m in re.finditer(r"\{([^{}]*)\}", raw):
        tok = m.group(1).strip()
        if tok:
            cands.append(tok)
    remainder = re.sub(r"\{[^{}]*\}", " ", raw)
    for tok in re.split(r"\s+", remainder):
        tok = tok.strip()
        # Skip text/uri-list comment lines and empty tokens.
        if tok and not tok.startswith("#"):
            cands.append(tok)
    return cands


def _time_param(text):
    """Return a '&t=...' fragment if the source URL carried a start time."""
    m = re.search(r"[?&](?:t|start)=([0-9hms]+)", text, re.IGNORECASE)
    return f"&t={m.group(1)}" if m else ""


def _find_direct_url(text):
    """A watch/page URL for a video, extracted from anywhere in ``text``.
    youtu.be / shorts / live / embed forms are rebuilt to the canonical
    ``youtube.com/watch?v=`` URL; everything else is returned as dragged."""
    m = re.search(r"https?://[^\s\"'<>]*youtube\.com/watch\?[^\s\"'<>]*", text, re.IGNORECASE)
    if m:
        return _clean(m.group(0))

    m = re.search(
        r"https?://[^\s\"'<>]*(?:youtu\.be/|youtube\.com/(?:shorts|live|embed|v)/)(" + _YT_ID + r")",
        text, re.IGNORECASE,
    )
    if m:
        rest = text[m.start():]
        return f"https://www.youtube.com/watch?v={m.group(1)}{_time_param(rest)}"

    m = re.search(
        r"https?://[^\s\"'<>]*(?:"
        r"vimeo\.com/\d+"
        r"|dailymotion\.com/video/[A-Za-z0-9]+"
        r"|dai\.ly/[A-Za-z0-9]+"
        r"|twitch\.tv/videos/\d+"
        r")[^\s\"'<>]*",
        text, re.IGNORECASE,
    )
    if m and not _IMG_EXT_RE.search(m.group(0)):
        return _clean(m.group(0))
    return None


def _reconstruct_from_thumb(text):
    """Turn a known thumbnail-image URL into its video page URL."""
    m = re.search(
        r"(?:i\d*\.ytimg\.com|img\.youtube\.com)/(?:vi|vi_webp)/(" + _YT_ID + r")/",
        text, re.IGNORECASE,
    )
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    m = re.search(r"vumbnail\.com/(\d+)\.jpg", text, re.IGNORECASE)
    if m:
        return f"https://vimeo.com/{m.group(1)}"
    return None


def extract_video_url(raw):
    """Best-effort single video/page URL from a raw tkdnd drop payload.
    Returns a URL string, or None only if no URL is present at all."""
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None

    candidates = []
    if _looks_like_html(raw):
        candidates.extend(_urls_from_html(raw))
    candidates.extend(_split_candidates(raw))
    candidates.append(raw)  # whole string, so the regexes can scan across it

    cleaned = []
    for c in candidates:
        c = _clean(c)
        if c and c not in cleaned:
            cleaned.append(c)

    # 1. A real watch/video page URL wins (this is the thumbnail's link target).
    for c in cleaned:
        u = _find_direct_url(c)
        if u:
            return u

    # 2. Rebuild a watch URL from a thumbnail image URL.
    for c in cleaned:
        u = _reconstruct_from_thumb(c)
        if u:
            return u

    # 3. Any http(s) URL that is not obviously a static image asset.
    #    When the candidate IS itself a URL (e.g. recovered from tkdnd's
    #    {...} wrapping), take it whole - re-running _URL_RE would truncate
    #    it at the first space, undoing the point of honoring the braces.
    for c in cleaned:
        cand = c if _is_http(c) else (_URL_RE.search(c).group(0)
                                      if _URL_RE.search(c) else None)
        if cand and not _IMG_EXT_RE.search(cand):
            return _clean(cand)

    # 4. Last resort: any http(s) URL at all - yt-dlp may still handle it,
    #    and if not, the fetch step gives the user a clear error.
    for c in cleaned:
        if _is_http(c):
            return _clean(c)
        m = _URL_RE.search(c)
        if m:
            return _clean(m.group(0))

    return None


if __name__ == "__main__":
    CASES = [
        # (raw payload, expected result)
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ",
         "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        ("{https://www.youtube.com/watch?v=dQw4w9WgXcQ}",
         "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        ('<a href="https://www.youtube.com/watch?v=abc123DEFgh">'
         '<img src="https://i.ytimg.com/vi/abc123DEFgh/hqdefault.jpg"></a>',
         "https://www.youtube.com/watch?v=abc123DEFgh"),
        ("https://i.ytimg.com/vi/abc123DEFgh/hqdefault.jpg",
         "https://www.youtube.com/watch?v=abc123DEFgh"),
        ("https://youtu.be/abc123DEFgh?si=xyz",
         "https://www.youtube.com/watch?v=abc123DEFgh"),
        # extra real-world payloads
        ("https://i9.ytimg.com/vi_webp/abc123DEFgh/maxresdefault.webp?sqp=x&rs=y",
         "https://www.youtube.com/watch?v=abc123DEFgh"),
        ("{https://www.youtube.com/shorts/abc123DEFgh}",
         "https://www.youtube.com/watch?v=abc123DEFgh"),
        ("https://youtu.be/abc123DEFgh?t=42",
         "https://www.youtube.com/watch?v=abc123DEFgh&t=42"),
        ('<a href="https://vimeo.com/123456789" class="c"><img src="https://i.vimeocdn.com/video/x_200.jpg"></a>',
         "https://vimeo.com/123456789"),
        ("https://vumbnail.com/123456789.jpg",
         "https://vimeo.com/123456789"),
        ("https://www.twitch.tv/videos/9988776655",
         "https://www.twitch.tv/videos/9988776655"),
        ("https://www.dailymotion.com/video/x8abcde",
         "https://www.dailymotion.com/video/x8abcde"),
        ("https://example.com/uri-list-page\r\n# a comment",
         "https://example.com/uri-list-page"),
        ("{https://i.ytimg.com/vi/abc123DEFgh/hqdefault.jpg} {https://www.youtube.com/watch?v=abc123DEFgh}",
         "https://www.youtube.com/watch?v=abc123DEFgh"),
        ("https://cdn.example.com/lonely-thumbnail.jpg",
         "https://cdn.example.com/lonely-thumbnail.jpg"),
        # a 12+ char slug must NOT be truncated to a different valid id
        ("https://youtu.be/abcdefghijkLMNOP",
         "https://youtu.be/abcdefghijkLMNOP"),
        # a real paren in the path is kept (balanced)
        ("https://en.wikipedia.org/wiki/Heat_(1995_film)",
         "https://en.wikipedia.org/wiki/Heat_(1995_film)"),
        # dragged out of a sentence - trailing period trimmed
        ("see https://example.com/clip.mp4.",
         "https://example.com/clip.mp4"),
        # brace-wrapped URL that contains a space is recovered whole
        ("{https://example.com/my file.mp4}",
         "https://example.com/my file.mp4"),
        ("not a url at all", None),
        ("", None),
        (None, None),
    ]
    failures = 0
    for raw, expected in CASES:
        got = extract_video_url(raw)
        ok = got == expected
        if not ok:
            failures += 1
        print(f"[{'ok ' if ok else 'FAIL'}] {raw!r}\n       -> {got!r}"
              + ("" if ok else f"   (expected {expected!r})"))
    print()
    if failures:
        raise SystemExit(f"{failures} self-test(s) FAILED")
    print(f"all {len(CASES)} drag-drop URL self-tests passed")
