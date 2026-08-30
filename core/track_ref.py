"""TrackRef - one music track's metadata, from Spotify or a pasted list.

Every field has a default so a partially-known track (e.g. from a pasted
"Artist - Title" line) is still a valid TrackRef.
"""
from dataclasses import dataclass, field


@dataclass
class TrackRef:
    title: str = ""
    artists: list = field(default_factory=list)      # list[str]
    album: str = ""
    album_artist: str = ""
    track_no: int = 0
    disc_no: int = 0
    total_tracks: int = 0
    release_date: str = ""                           # "YYYY", "YYYY-MM", or "YYYY-MM-DD"
    duration_ms: int = 0
    isrc: str = ""
    upc: str = ""
    cover_url: str = ""
    explicit: bool = False
    spotify_id: str = ""
    album_type: str = ""                             # "album" | "single" | "compilation"

    @property
    def artist_str(self) -> str:
        return ", ".join(a for a in self.artists if a)

    @property
    def search_query(self) -> str:
        """A YouTube search string for this track."""
        primary = self.artists[0] if self.artists else ""
        return f"{primary} {self.title}".strip()

    @property
    def year(self) -> str:
        return self.release_date[:4] if self.release_date else ""
