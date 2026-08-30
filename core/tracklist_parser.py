"""Parse a pasted track list or CSV into TrackRef objects (text fields only)."""
import csv
import io
from core.track_ref import TrackRef


def parse_tracklist(text: str) -> list[TrackRef]:
    """Parse pasted track list or CSV into TrackRef objects.

    Rules:
    - Ignore blank lines and lines starting with "#".
    - If first non-comment line looks like CSV header: parse as CSV.
    - Otherwise treat each line as "Artist - Title".
    - Return list[TrackRef].
    """
    lines = text.strip().split("\n")

    # Filter out blank lines and comments
    non_comment_lines = [
        line for line in lines if line.strip() and not line.strip().startswith("#")
    ]

    if not non_comment_lines:
        return []

    # Check if first line looks like a CSV header
    first_line = non_comment_lines[0]
    is_csv = _looks_like_csv_header(first_line)

    if is_csv:
        return _parse_csv(text)
    else:
        return _parse_artist_title_lines(non_comment_lines)


def _looks_like_csv_header(line: str) -> bool:
    """Check if a line looks like a CSV header."""
    if "," not in line:
        return False
    line_lower = line.lower()
    return any(
        word in line_lower for word in ["title", "track", "artist"]
    )


def _parse_csv(text: str) -> list[TrackRef]:
    """Parse CSV text into TrackRef objects."""
    results = []
    try:
        # Use csv.DictReader to parse
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return []

        # Build case-insensitive key map
        key_map = {name.lower(): name for name in reader.fieldnames}

        for row in reader:
            # Create case-insensitive access
            row_lower = {k.lower(): v for k, v in row.items()}

            title = row_lower.get("title", "").strip()
            track_no_str = row_lower.get("track_no", row_lower.get("track", "")).strip()
            disc_no_str = row_lower.get("disc_no", row_lower.get("disc", "")).strip()
            album = row_lower.get("album", "").strip()
            album_artist = row_lower.get("album_artist", "").strip()
            isrc = row_lower.get("isrc", "").strip()

            # Parse artist field (may be "a; b" or "a, b")
            artist_field = row_lower.get("artist", "").strip()
            artists = []
            if artist_field:
                # Try splitting by semicolon first, then by comma
                if ";" in artist_field:
                    artists = [a.strip() for a in artist_field.split(";")]
                elif "," in artist_field:
                    artists = [a.strip() for a in artist_field.split(",")]
                else:
                    artists = [artist_field]

            # Skip if no title
            if not title:
                continue

            track_no = int(track_no_str) if track_no_str else 0
            disc_no = int(disc_no_str) if disc_no_str else 0

            ref = TrackRef(
                title=title,
                artists=artists,
                album=album,
                album_artist=album_artist,
                track_no=track_no,
                disc_no=disc_no,
                isrc=isrc,
            )
            results.append(ref)

    except Exception:
        pass

    return results


def _parse_artist_title_lines(lines: list[str]) -> list[TrackRef]:
    """Parse lines in 'Artist - Title' format."""
    results = []
    dashes = ["-", "–", "—", " — "]  # hyphen, en-dash, em-dash, unicode arrow

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Try to find a dash
        artist = ""
        title = ""
        found_dash = False

        for dash in dashes:
            if dash in line:
                parts = line.split(dash, 1)
                artist = parts[0].strip()
                title = parts[1].strip() if len(parts) > 1 else ""
                found_dash = True
                break

        if not found_dash:
            # No dash found, whole line is title
            title = line

        # Skip if no title
        if not title:
            continue

        artists = [artist] if artist else []
        ref = TrackRef(title=title, artists=artists)
        results.append(ref)

    return results
