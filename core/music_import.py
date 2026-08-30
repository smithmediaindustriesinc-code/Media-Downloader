"""Orchestrates a "music service import": resolve a Spotify link (or a
pasted 'Artist - Title' / CSV list) to a track list, find each track on
YouTube, and remember what was downloaded so a playlist can be re-synced.

The audio is a YouTube match, NOT the Spotify recording - callers must
tell the user that.
"""
import json
import os
import time
from dataclasses import dataclass, field

from core.paths import app_dir
from core.spotify_client import parse_spotify_ref, SpotifyError
from core.tracklist_parser import parse_tracklist
from core.track_match import find_match


@dataclass
class MatchedTrack:
    """A TrackRef matched to a YouTube video."""
    ref: "TrackRef"
    result: "MatchResult"          # from track_match
    selected: bool = True          # user can deselect in the review UI
    override_url: str = ""          # user pasted a YouTube URL for this row

    @property
    def download_url(self) -> str:
        """Return the URL to download: override_url, result.best.url, or ''."""
        if self.override_url:
            return self.override_url
        if self.result.best:
            return self.result.best.url
        return ""

    @property
    def status(self) -> str:
        """Return status from result: 'confident', 'ambiguous', or 'none'."""
        return self.result.status


@dataclass
class ImportSession:
    """A single music import session with metadata and matched tracks."""
    source_text: str
    kind: str                       # spotify kind or "text"
    name: str
    spotify_id: str = ""
    snapshot_id: str = ""
    tracks: list = field(default_factory=list)   # list[MatchedTrack]


def resolve_source(text: str, spotify_client=None) -> tuple[str, str, str, str, list]:
    """Resolve a Spotify reference or pasted text to a track list.

    Args:
        text: Spotify link, URI, or pasted text (e.g. "Artist - Title\n...")
        spotify_client: SpotifyClient instance, required for Spotify refs

    Returns:
        (kind, name, spotify_id, snapshot_id, list[TrackRef])
        - kind is "spotify" + subtype or "text"
        - name is a display name
        - spotify_id and snapshot_id are empty for text imports

    Raises:
        ValueError if Spotify link given but no client, or text has no tracks
        SpotifyError if Spotify API fails
    """
    text = text.strip()

    # Is this a Spotify reference? Only the PARSE failure means "not Spotify" -
    # a resolve() failure on a valid link must propagate, not silently fall
    # through to treating the URL as a pasted track list.
    is_spotify = True
    try:
        parse_spotify_ref(text)
    except SpotifyError:
        is_spotify = False

    if is_spotify:
        if spotify_client is None or not spotify_client.is_connected:
            raise ValueError(
                "Connect Spotify first (Settings) to import a Spotify link.")
        result = spotify_client.resolve(text)   # SpotifyError propagates
        return (result.kind, result.name, result.spotify_id,
                result.snapshot_id, result.tracks)

    # Treat as a pasted list
    tracks = parse_tracklist(text)
    if not tracks:
        raise ValueError("No tracks found in that text.")
    return ("text", "Pasted list", "", "", tracks)


def match_tracks(tracks: list, cfg: dict | None = None, progress_cb=None,
                 cancel_event=None) -> list:
    """Match each track to a YouTube video.

    Args:
        tracks: list[TrackRef]
        cfg: optional config dict for track_match.find_match
        progress_cb: optional callback(index, total, ref) called after each match
        cancel_event: optional threading.Event; if set, stop matching

    Returns:
        list[MatchedTrack], one per input track (in order)
    """
    result = []
    for i, ref in enumerate(tracks):
        if cancel_event is not None and cancel_event.is_set():
            break
        match_result = find_match(ref, cfg)
        result.append(MatchedTrack(ref=ref, result=match_result))
        if progress_cb:
            progress_cb(i + 1, len(tracks), ref)
    return result


def build_session(text, spotify_client=None, cfg=None, progress_cb=None,
                  cancel_event=None) -> ImportSession:
    """Resolve source and match all tracks in one call.

    Args:
        text: Spotify link or pasted text
        spotify_client: optional SpotifyClient
        cfg: optional config dict for track matching
        progress_cb: optional callback for match progress
        cancel_event: optional threading.Event to cancel

    Returns:
        ImportSession with matched tracks

    Raises:
        ValueError or SpotifyError on input/connectivity issues
    """
    kind, name, spotify_id, snapshot_id, tracks = resolve_source(text, spotify_client)
    matched = match_tracks(tracks, cfg, progress_cb, cancel_event)
    return ImportSession(
        source_text=text,
        kind=kind,
        name=name,
        spotify_id=spotify_id,
        snapshot_id=snapshot_id,
        tracks=matched
    )


# ============================================================================
# Persistence: <app_dir>/music_imports.json
# ============================================================================

def _store_path() -> str:
    """Path to the imports store file."""
    return os.path.join(app_dir(), "music_imports.json")


def load_imports() -> list:
    """Load all saved imports.

    Returns:
        list of import records, or [] if file missing/broken

    Never raises.
    """
    try:
        path = _store_path()
        if not os.path.isfile(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("imports") or []
    except Exception:
        return []


def get_import(import_id: str) -> dict | None:
    """Get a single import record by ID.

    Args:
        import_id: the import record's id

    Returns:
        dict record or None if not found
    """
    for record in load_imports():
        if record.get("id") == import_id:
            return record
    return None


def save_import(record: dict) -> None:
    """Save or update an import record.

    If record has an 'id' matching an existing one, replace it; else append.
    Fills in 'id' and 'date' if missing.

    Args:
        record: dict with import data

    Never raises - wraps file I/O in try/except.
    """
    try:
        # Set defaults
        if "date" not in record:
            record["date"] = time.strftime("%Y-%m-%d")
        if "id" not in record:
            spotify_id = record.get("spotify_id", "")
            prefix = spotify_id if spotify_id else "text"
            record["id"] = f"{prefix}-{int(time.time())}"

        path = _store_path()
        imports = load_imports()

        # Replace existing or append
        found = False
        for i, existing in enumerate(imports):
            if existing.get("id") == record.get("id"):
                imports[i] = record
                found = True
                break
        if not found:
            imports.append(record)

        # Write atomically
        tmp_path = path + ".tmp"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"imports": imports}, f, indent=2)
        os.replace(tmp_path, path)

    except Exception:
        pass


def record_from_session(session, folder: str,
                        downloaded: list[tuple]) -> dict:
    """Build an import record from a session and downloaded files.

    Args:
        session: ImportSession
        folder: where files were saved
        downloaded: list of (MatchedTrack, file_path) tuples

    Returns:
        dict with schema: {id, kind, name, spotify_id, snapshot_id, date, folder, tracks: [...]}
    """
    # Build track records
    tracks_records = []
    # Map MatchedTrack by index since they're not hashable
    downloaded_by_idx = {}
    for idx, (matched_track, fpath) in enumerate(downloaded):
        # Find the index of this matched_track in session.tracks
        for j, mt in enumerate(session.tracks):
            if mt is matched_track:
                downloaded_by_idx[j] = fpath
                break

    for i, matched_track in enumerate(session.tracks):
        ref = matched_track.ref
        file_path = downloaded_by_idx.get(i, "")
        tracks_records.append({
            "spotify_id": ref.spotify_id,
            "isrc": ref.isrc,
            "title": ref.title,
            "artist": ref.artist_str,
            "yt_url": matched_track.download_url,
            "file": file_path
        })

    # "saved" (Liked Songs) has no Spotify id but is still re-syncable - use a
    # sentinel so diff_for_resync recognises it.
    resync_id = session.spotify_id or ("saved" if session.kind == "saved" else "")

    # Build the record
    record = {
        "id": f"{resync_id or 'text'}-{int(time.time())}",
        "kind": session.kind,
        "name": session.name,
        "spotify_id": resync_id,
        "snapshot_id": session.snapshot_id,
        "date": time.strftime("%Y-%m-%d"),
        "folder": folder,
        "tracks": tracks_records
    }
    return record


def diff_for_resync(import_id: str, spotify_client, cfg=None,
                    progress_cb=None) -> dict:
    """Compare a saved import to the current Spotify state.

    Args:
        import_id: the saved import's ID
        spotify_client: SpotifyClient instance
        cfg: optional config for track matching
        progress_cb: optional callback for match progress

    Returns:
        {
            "new": list[MatchedTrack] for new tracks since import,
            "removed": list[dict] for tracks no longer in Spotify,
            "unchanged": int count of tracks unchanged,
            "snapshot_changed": bool whether playlist snapshot changed
        }

    Raises:
        ValueError if import can't be re-synced (not a Spotify import)
        SpotifyError if Spotify API fails
    """
    record = get_import(import_id)
    if not record or not record.get("spotify_id"):
        raise ValueError("This import can't be re-synced (not a Spotify playlist).")

    # Re-resolve from Spotify
    spotify_id = record["spotify_id"]
    kind = record.get("kind", "")

    # Build the URI based on kind
    if kind == "saved" or spotify_id == "saved":
        uri = "saved"
    elif kind in ("playlist", "album", "artist", "track"):
        uri = f"spotify:{kind}:{spotify_id}"
    else:
        uri = f"spotify:playlist:{spotify_id}"  # default guess

    resolved = spotify_client.resolve(uri)
    new_refs = resolved.tracks

    # Build key set from saved tracks (isrc or spotify_id)
    known_keys = set()
    for track_row in record.get("tracks") or []:
        key = track_row.get("isrc") or track_row.get("spotify_id")
        if key:
            known_keys.add(key.lower())

    # Find new tracks
    new_list = []
    for ref in new_refs:
        key = (ref.isrc or ref.spotify_id or "").lower()
        if key and key in known_keys:
            continue
        new_list.append(ref)

    # Find removed tracks
    removed_list = []
    for track_row in record.get("tracks") or []:
        key = (track_row.get("isrc") or track_row.get("spotify_id") or "").lower()
        # Check if this key is in the new resolved set
        found = False
        for ref in new_refs:
            ref_key = (ref.isrc or ref.spotify_id or "").lower()
            if ref_key and ref_key == key:
                found = True
                break
        if not found:
            removed_list.append(track_row)

    # Match new tracks
    matched_new = match_tracks(new_list, cfg, progress_cb)

    # Count unchanged
    unchanged_count = len(new_refs) - len(new_list)

    return {
        "new": matched_new,
        "removed": removed_list,
        "unchanged": unchanged_count,
        "snapshot_changed": resolved.snapshot_id != record.get("snapshot_id", "")
    }
