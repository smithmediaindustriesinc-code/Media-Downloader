"""Write audio tags to MP3, FLAC, M4A, Ogg/Opus tracks based on TrackRef metadata."""
import base64
import io
import urllib.request
from pathlib import Path

from mutagen.id3 import ID3, TIT2, TPE1, TALB, TPE2, TRCK, TPOS, TDRC, TSRC, COMM, APIC, USLT
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

import mutagen.id3


def fetch_cover_bytes(url: str, max_px: int = 800) -> bytes | None:
    """Download url, convert to RGB JPEG, thumbnail to max_px, return bytes or None."""
    try:
        from PIL import Image

        req = urllib.request.Request(url, headers={"User-Agent": "MediaDownloader"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        img = Image.open(io.BytesIO(raw))
        img = img.convert("RGB")
        img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return buf.getvalue()
    except Exception:
        return None


def fetch_lyrics(ref: "TrackRef") -> str | None:  # noqa: F821
    """Fetch lyrics from syncedlyrics. Returns LRC or plain text or None."""
    try:
        import syncedlyrics

        query = f"{ref.artist_str} {ref.title}".strip()
        result = syncedlyrics.search(query)
        return result if result else None
    except Exception:
        return None


def write_tags(
    path: str,
    ref: "TrackRef",  # noqa: F821
    *,
    embed_cover: bool = True,
    cover_max_px: int = 800,
    source_comment: bool = True,
    lyrics: str | None = None,
) -> tuple[bool, str]:
    """Write tags to audio file. Return (success, message)."""
    try:
        path_obj = Path(path)
        ext = path_obj.suffix.lower()

        # Prepare cover if needed
        cover_bytes = None
        if embed_cover and ref.cover_url:
            cover_bytes = fetch_cover_bytes(ref.cover_url, cover_max_px)

        # Prepare comment
        comment_text = (
            "Matched from YouTube by Media Downloader - not the original streaming service audio."
            if source_comment
            else ""
        )

        if ext == ".mp3":
            try:
                tags = ID3(path)
            except mutagen.id3.ID3NoHeaderError:
                tags = ID3()

            tags["TIT2"] = TIT2(encoding=3, text=[ref.title])
            tags["TALB"] = TALB(encoding=3, text=[ref.album])
            tags["TPE2"] = TPE2(encoding=3, text=[ref.album_artist])

            # Join artists with "; " for TPE1
            if ref.artists:
                tags["TPE1"] = TPE1(encoding=3, text=["; ".join(ref.artists)])

            if ref.track_no:
                track_str = str(ref.track_no)
                if ref.total_tracks:
                    track_str = f"{ref.track_no}/{ref.total_tracks}"
                tags["TRCK"] = TRCK(encoding=3, text=[track_str])

            if ref.disc_no:
                tags["TPOS"] = TPOS(encoding=3, text=[str(ref.disc_no)])

            # Date: prefer release_date, fall back to year
            date_str = ref.release_date if ref.release_date else ref.year
            if date_str:
                tags["TDRC"] = TDRC(encoding=3, text=[date_str])

            if ref.isrc:
                tags["TSRC"] = TSRC(encoding=3, text=[ref.isrc])

            if comment_text:
                tags["COMM"] = COMM(encoding=3, lang="eng", desc="", text=[comment_text])

            if cover_bytes:
                tags["APIC"] = APIC(
                    encoding=3,
                    mime="image/jpeg",
                    type=3,  # Front cover
                    desc="",
                    data=cover_bytes,
                )

            if lyrics:
                tags["USLT"] = USLT(encoding=3, lang="eng", desc="", text=[lyrics])

            tags.save(path)

        elif ext == ".flac":
            f = FLAC(path)

            if ref.title:
                f["title"] = [ref.title]
            if ref.album:
                f["album"] = [ref.album]
            if ref.album_artist:
                f["albumartist"] = [ref.album_artist]
            if ref.artists:
                f["artist"] = ref.artists
            if ref.track_no:
                f["tracknumber"] = [str(ref.track_no)]
            if ref.total_tracks:
                f["totaltracks"] = [str(ref.total_tracks)]
            if ref.disc_no:
                f["discnumber"] = [str(ref.disc_no)]

            date_str = ref.release_date if ref.release_date else ref.year
            if date_str:
                f["date"] = [date_str]

            if ref.isrc:
                f["isrc"] = [ref.isrc]

            if comment_text:
                f["comment"] = [comment_text]

            if lyrics:
                f["lyrics"] = [lyrics]

            if cover_bytes:
                pic = Picture()
                pic.type = 3  # Front cover
                pic.mime = "image/jpeg"
                pic.data = cover_bytes
                f.add_picture(pic)

            f.save()

        elif ext in (".m4a", ".mp4", ".aac"):
            m = MP4(path)

            if ref.title:
                m["\xa9nam"] = [ref.title]
            if ref.album:
                m["\xa9alb"] = [ref.album]
            if ref.album_artist:
                m["aART"] = [ref.album_artist]
            if ref.artists:
                m["\xa9ART"] = ref.artists

            if ref.track_no or ref.total_tracks:
                m["trkn"] = [(ref.track_no, ref.total_tracks)]

            if ref.disc_no:
                m["disk"] = [(ref.disc_no, 0)]

            date_str = ref.release_date if ref.release_date else ref.year
            if date_str:
                m["\xa9day"] = [date_str]

            if ref.isrc:
                m["ISRC"] = [ref.isrc]

            if comment_text:
                m["\xa9cmt"] = [comment_text]

            if lyrics:
                m["\xa9lyr"] = [lyrics]

            if cover_bytes:
                m["covr"] = [MP4Cover(cover_bytes, MP4Cover.FORMAT_JPEG)]

            m.save()

        elif ext in (".opus", ".ogg"):
            # Use OggOpus if .opus, OggVorbis otherwise
            if ext == ".opus":
                audio = OggOpus(path)
            else:
                audio = OggVorbis(path)

            if ref.title:
                audio["title"] = [ref.title]
            if ref.album:
                audio["album"] = [ref.album]
            if ref.album_artist:
                audio["albumartist"] = [ref.album_artist]
            if ref.artists:
                audio["artist"] = ref.artists

            if ref.track_no:
                audio["tracknumber"] = [str(ref.track_no)]
            if ref.total_tracks:
                audio["totaltracks"] = [str(ref.total_tracks)]
            if ref.disc_no:
                audio["discnumber"] = [str(ref.disc_no)]

            date_str = ref.release_date if ref.release_date else ref.year
            if date_str:
                audio["date"] = [date_str]

            if ref.isrc:
                audio["isrc"] = [ref.isrc]

            if comment_text:
                audio["comment"] = [comment_text]

            if lyrics:
                audio["lyrics"] = [lyrics]

            if cover_bytes:
                pic = Picture()
                pic.type = 3
                pic.mime = "image/jpeg"
                pic.data = cover_bytes
                audio["METADATA_BLOCK_PICTURE"] = [
                    base64.b64encode(pic.write()).decode("ascii")
                ]

            audio.save()

        else:
            return (False, f"Unsupported format: {ext}")

        return (True, f"tagged {path_obj.name}")

    except Exception as e:
        return (False, f"Error tagging {path}: {str(e)}")
