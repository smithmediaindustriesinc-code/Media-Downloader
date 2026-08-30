"""Match a TrackRef to a YouTube / YouTube Music video for downloading.

The audio Media Downloader produces for a "Spotify import" is this YouTube
match - NOT the Spotify master. The caller must surface that to the user.
"""
from dataclasses import dataclass
import re
from typing import Optional
from urllib.parse import quote_plus

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None

# Module-level constants (caller may override via cfg arg)
DEFAULT_SOURCE_PRIORITY = ["ytmusic", "youtube"]
DEFAULT_DURATION_TOLERANCE_S = 4
DEFAULT_MIN_CONFIDENCE = 0.55
DEFAULT_REJECT_KEYWORDS = [
    "live", "cover", "remix", "sped up", "spedup",
    "nightcore", "karaoke", "instrumental", "8d audio", "reverb", "slowed",
    "reaction", "lyrics video", "1 hour", "loop"
]
DEFAULT_PREFER_TOPIC = True
SEARCH_RESULTS_PER_SOURCE = 6


@dataclass
class MatchCandidate:
    """A potential match for a track on YouTube/YouTube Music."""
    url: str
    title: str
    channel: str
    duration_s: int
    source: str            # "ytmusic" | "youtube"
    score: float           # 0.0 - 1.0


@dataclass
class MatchResult:
    """Result of finding a match for a track."""
    status: str            # "confident" | "ambiguous" | "none"
    best: Optional[MatchCandidate]
    candidates: list       # list[MatchCandidate], best-first
    query: str


def _norm(s: str) -> str:
    """Normalize a string for comparison.

    Lowercase, strip, collapse whitespace, drop bracketed noise like
    "(official video)" or "[HD]".
    """
    if not s:
        return ""

    # Lowercase and strip
    s = s.lower().strip()

    # Remove bracketed content: [HD], (official video), etc.
    s = re.sub(r'\[.*?\]', '', s)
    s = re.sub(r'\(.*?\)', '', s)

    # Collapse whitespace
    s = re.sub(r'\s+', ' ', s).strip()

    return s


def _search(query: str, source: str, limit: int) -> list:
    """Search YouTube or YouTube Music for a track.

    Args:
        query: Search query string
        source: "youtube" or "ytmusic"
        limit: Number of results to fetch

    Returns:
        list of dicts with title, uploader (channel), duration_string, url, id
        or [] on any exception (never raises)
    """
    if not yt_dlp:
        return []

    try:
        # yt-dlp has no first-class YouTube Music search; both sources use its
        # ytsearch. The "ytmusic" pass asks for a few extra results and the
        # scorer's Topic/VEVO/official-artist channel bonus does the
        # "prefer the real master" work. Kept as two passes so a future
        # proper YT Music extractor can slot in without touching callers.
        n = limit + 2 if source == "ytmusic" else limit
        search_url = f"ytsearch{n}:{query}"

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "noplaylist": True,
            "socket_timeout": 15,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_url, download=False)
            entries = info.get("entries") or []
            return list(entries)

    except Exception:
        return []


def _score(ref, entry: dict, cfg: dict) -> float:
    """Score a candidate entry against the track reference.

    Score is in [0, 1]:
      0.45 * title similarity (token_set_ratio)
      0.25 * artist similarity (partial_ratio of artist_str vs title+channel)
      0.20 * duration closeness
      0.10 * channel bonus (- Topic, VEVO, artist name)
      minus 0.50 if reject keywords found (clamped at 0)

    Args:
        ref: TrackRef object with title, artists, duration_ms
        entry: dict with title, uploader (channel), duration, url
        cfg: config dict with reject_keywords, prefer_topic_channels,
             duration_tolerance_s

    Returns:
        float score rounded to 4 decimals, 0.0 - 1.0
    """
    if not fuzz:
        return 0.0

    reject_keywords = cfg.get("reject_keywords", DEFAULT_REJECT_KEYWORDS)
    prefer_topic = cfg.get("prefer_topic_channels", DEFAULT_PREFER_TOPIC)
    duration_tol_s = cfg.get("duration_tolerance_s", DEFAULT_DURATION_TOLERANCE_S)

    score = 0.0

    # Get original and normalized titles (before normalizing for keyword check)
    entry_title_original = entry.get("title", "")
    norm_ref_title = _norm(ref.title)
    norm_entry_title = _norm(entry_title_original)

    # Check for reject keywords BEFORE normalizing (to catch keywords in parens)
    norm_entry_title_for_keywords = entry_title_original.lower()
    norm_ref_title_for_keywords = ref.title.lower()

    # 0.45 * title similarity
    if norm_ref_title and norm_entry_title:
        title_sim = fuzz.token_set_ratio(norm_ref_title, norm_entry_title) / 100.0
        score += 0.45 * title_sim
    else:
        score += 0.0

    # 0.25 * artist similarity
    ref_artist_str = ref.artist_str
    entry_uploader = entry.get("uploader", "") or ""
    entry_channel = entry.get("channel", "") or ""
    combined_channel = f"{norm_entry_title} {entry_channel}".strip()

    if ref_artist_str and combined_channel:
        artist_sim = fuzz.partial_ratio(ref_artist_str.lower(), combined_channel) / 100.0
        score += 0.25 * artist_sim
    else:
        score += 0.0

    # 0.20 * duration closeness
    ref_duration_s = ref.duration_ms / 1000 if ref.duration_ms else None
    entry_duration_s = entry.get("duration")

    if ref_duration_s and entry_duration_s:
        delta = abs(entry_duration_s - ref_duration_s)
        if delta <= duration_tol_s:
            duration_score = 1.0
        elif delta > 30:
            duration_score = 0.0
        else:
            # Linear ramp from 1.0 at delta=tol to 0.0 at delta=30
            duration_score = max(0.0, 1.0 - (delta - duration_tol_s) / (30 - duration_tol_s))
        score += 0.20 * duration_score
    else:
        # Unknown duration -> 0.10 neutral partial credit
        score += 0.10

    # 0.10 * channel bonus
    channel_bonus = 0.0
    if entry_channel:
        norm_channel = _norm(entry_channel)
        # Topic channel bonus (auto-generated masters)
        if prefer_topic and "topic" in norm_channel:
            channel_bonus = 1.0
        # VEVO bonus (official music videos)
        elif "vevo" in norm_channel.lower():
            channel_bonus = 1.0
        # Artist name bonus
        elif ref_artist_str and ref_artist_str.lower() in norm_channel:
            channel_bonus = 1.0

    score += 0.10 * channel_bonus

    # Penalty: reject keywords found in title but not in ref
    # Check the original (non-normalized) text to catch keywords in parentheses
    for keyword in reject_keywords:
        if keyword.lower() in norm_entry_title_for_keywords and keyword.lower() not in norm_ref_title_for_keywords:
            score = max(0.0, score - 0.5)

    return round(score, 4)


def find_match(ref, cfg: Optional[dict] = None) -> MatchResult:
    """Find the best YouTube/YouTube Music match for a track.

    Args:
        ref: TrackRef object
        cfg: optional config dict with keys:
          - source_priority: list of sources to search (default: ["ytmusic", "youtube"])
          - duration_tolerance_s: max duration delta in seconds (default: 4)
          - min_confidence: minimum score for "confident" status (default: 0.55)
          - reject_keywords: list of keywords to penalize (default: DEFAULT_REJECT_KEYWORDS)
          - prefer_topic_channels: prefer auto-generated "- Topic" channels (default: True)
          - results_per_source: number of results per search (default: SEARCH_RESULTS_PER_SOURCE)

    Returns:
        MatchResult with status ("confident" | "ambiguous" | "none"), best candidate,
        all candidates sorted by score (best first), and the search query.

    Never raises - returns status "none" on any error.
    """
    if cfg is None:
        cfg = {}

    source_priority = cfg.get("source_priority", DEFAULT_SOURCE_PRIORITY)
    min_confidence = cfg.get("min_confidence", DEFAULT_MIN_CONFIDENCE)
    results_per_source = cfg.get("results_per_source", SEARCH_RESULTS_PER_SOURCE)

    query = ref.search_query

    all_candidates = []
    seen_ids = set()

    try:
        # Search each source in priority order
        for source in source_priority:
            try:
                entries = _search(query, source, results_per_source)

                for entry in entries:
                    # Extract video id to dedupe
                    vid_id = entry.get("id") or entry.get("url", "")
                    if vid_id in seen_ids:
                        continue
                    seen_ids.add(vid_id)

                    # Score this candidate
                    score_val = _score(ref, entry, cfg)

                    # Build URL
                    url = entry.get("url", f"https://www.youtube.com/watch?v={vid_id}")

                    candidate = MatchCandidate(
                        url=url,
                        title=entry.get("title", ""),
                        channel=entry.get("channel") or entry.get("uploader", ""),
                        duration_s=entry.get("duration") or 0,
                        source=source,
                        score=score_val
                    )
                    all_candidates.append(candidate)

            except Exception:
                # Continue to next source on any error
                continue

        # Sort by score (descending)
        all_candidates.sort(key=lambda c: c.score, reverse=True)

        # Determine status
        if not all_candidates:
            return MatchResult(
                status="none",
                best=None,
                candidates=[],
                query=query
            )

        best = all_candidates[0]

        if best.score < min_confidence:
            return MatchResult(
                status="none",
                best=None,
                candidates=all_candidates,
                query=query
            )

        # Ambiguity: a close runner-up means "not sure which upload" - flag it
        # for the user to pick. But a very strong best (>= 0.88) is almost
        # certainly the right *song*; which particular official upload wins
        # doesn't matter, so don't demote those to a manual review.
        is_ambiguous = False
        if best.score < 0.88 and len(all_candidates) > 1:
            if best.score - all_candidates[1].score <= 0.08:
                is_ambiguous = True

        status = "ambiguous" if is_ambiguous else "confident"

        return MatchResult(
            status=status,
            best=best,
            candidates=all_candidates,
            query=query
        )

    except Exception:
        # NEVER raise - return "none" on any error
        return MatchResult(
            status="none",
            best=None,
            candidates=[],
            query=query
        )


def match_query(text_query: str, cfg: Optional[dict] = None) -> MatchResult:
    """Convenience wrapper: search a raw text string for a track.

    If text_query contains " - ", split it as "artist - title".
    Otherwise, treat the whole string as the title.

    Args:
        text_query: String like "Artist - Title" or just "Title"
        cfg: optional config dict (passed to find_match)

    Returns:
        MatchResult from find_match
    """
    from core.track_ref import TrackRef

    text_query = text_query.strip()

    # Try to parse as "artist - title"
    if " - " in text_query:
        parts = text_query.split(" - ", 1)
        artist = parts[0].strip()
        title = parts[1].strip()
        ref = TrackRef(title=title, artists=[artist] if artist else [])
    else:
        # Whole string is the title
        ref = TrackRef(title=text_query)

    return find_match(ref, cfg)
