"""F4 (1.7.4): move a finished download into sub-folders by a rule.

organize_path(base_dir, final_path, cfg, media_info) -> new absolute path
(the file is moved there). Returns the original path unchanged on 'off',
on any error, or if the destination would equal the source.
"""
import datetime
import os
import re
import shutil

from core.utils import sanitize_filename


def _source(media_info):
    for k in ("channel", "uploader", "playlist_uploader", "extractor_key"):
        v = (media_info or {}).get(k)
        if v:
            return sanitize_filename(str(v))[:60]
    return "Other"


def _date(media_info):
    ud = (media_info or {}).get("upload_date")  # YYYYMMDD
    if ud and len(str(ud)) == 8:
        return f"{ud[:4]}-{ud[4:6]}"
    return datetime.datetime.now().strftime("%Y-%m")


def _resolution(media_info):
    h = (media_info or {}).get("height")
    if not h:
        for f in (media_info or {}).get("formats") or []:
            h = max(h or 0, f.get("height") or 0)
    if not h:
        return "unknown"
    for cap, label in ((2160, "2160p"), (1440, "1440p"), (1080, "1080p"),
                       (720, "720p"), (480, "480p"), (360, "360p")):
        if h >= cap:
            return label
    return f"{h}p"


def _apply_pattern(pattern, final_path, media_info, dtype):
    stem, ext = os.path.splitext(os.path.basename(final_path))
    tokens = {
        "source": _source(media_info),
        "date": _date(media_info),
        "title": sanitize_filename(stem)[:80],
        "height": _resolution(media_info),
        "ext": ext.lstrip(".").lower(),
        "type": (dtype or "").lower() or "media",
    }

    def sub(m):
        return sanitize_filename(str(tokens.get(m.group(1), ""))) or "_"
    rel = re.sub(r"\{(\w+)\}", sub, pattern)
    rel = rel.replace("\\", "/")
    parts = [p for p in rel.split("/") if p and p not in (".", "..")]
    return os.path.join(*parts) if parts else ""


def organize_path(base_dir, final_path, cfg, media_info=None, dtype=None):
    mode = cfg.get("organize_mode", "off")
    if mode == "off" or not final_path or not os.path.isfile(final_path):
        return final_path
    try:
        if mode == "by_source":
            rel = _source(media_info)
        elif mode == "by_date":
            rel = _date(media_info)
        elif mode == "by_resolution":
            rel = _resolution(media_info)
        elif mode == "pattern":
            rel = _apply_pattern(cfg.get("organize_pattern", "{source}/{date}"),
                                 final_path, media_info, dtype)
        else:
            return final_path
        if not rel:
            return final_path
        dest_dir = os.path.join(base_dir, rel)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, os.path.basename(final_path))
        if os.path.abspath(dest) == os.path.abspath(final_path):
            return final_path
        # avoid clobbering
        n = 1
        stem, ext = os.path.splitext(dest)
        while os.path.exists(dest):
            dest = f"{stem} ({n}){ext}"
            n += 1
        shutil.move(final_path, dest)
        return dest
    except (OSError, ValueError):
        return final_path
