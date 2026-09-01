"""F13 (1.7.4): named metadata templates the user can apply in bulk to
manually-downloaded audio (podcasts, DJ sets, non-Spotify series) - tagging
was previously Spotify-import-only.

Templates live in cfg["tag_templates"] as a list of dicts with keys:
name, artist, album, album_artist, genre, year, comment.
Only non-empty fields are written; nothing is cleared.
"""

FIELDS = ("artist", "album", "album_artist", "genre", "year", "comment")
_AUDIO_EXT = (".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wav")


def _norm(name):
    return (name or "").strip()


def list_templates(cfg):
    return [t for t in (cfg.get("tag_templates") or [])
            if isinstance(t, dict) and _norm(t.get("name"))]


def template_names(cfg):
    return [t["name"] for t in list_templates(cfg)]


def get_template(cfg, name):
    name = _norm(name)
    for t in list_templates(cfg):
        if t["name"] == name:
            return t
    return None


def save_template(cfg, template):
    name = _norm(template.get("name"))
    if not name:
        raise ValueError("A template needs a name.")
    clean = {"name": name}
    for f in FIELDS:
        clean[f] = str(template.get(f, "") or "").strip()
    others = [t for t in (cfg.get("tag_templates") or []) if _norm(t.get("name")) != name]
    others.append(clean)
    others.sort(key=lambda t: t["name"].lower())
    cfg["tag_templates"] = others
    return clean


def delete_template(cfg, name):
    name = _norm(name)
    before = len(cfg.get("tag_templates") or [])
    cfg["tag_templates"] = [t for t in (cfg.get("tag_templates") or [])
                            if _norm(t.get("name")) != name]
    return len(cfg["tag_templates"]) != before


def apply_template(path, template):
    """Write the template's non-empty fields onto one audio file.
    Returns (ok: bool, message: str). Never raises."""
    import os
    if not path or not os.path.isfile(path):
        return False, "file not found"
    if os.path.splitext(path)[1].lower() not in _AUDIO_EXT:
        return False, "not an audio file"
    try:
        import mutagen
        audio = mutagen.File(path, easy=True)
        if audio is None:
            return False, "unsupported format"
        mapping = {
            "artist": "artist",
            "album": "album",
            "album_artist": "albumartist",
            "genre": "genre",
            "year": "date",
        }
        wrote = 0
        for tf, key in mapping.items():
            val = str(template.get(tf, "") or "").strip()
            if val:
                try:
                    audio[key] = val
                    wrote += 1
                except Exception:
                    pass
        comment = str(template.get("comment", "") or "").strip()
        if comment:
            try:
                audio["comment"] = comment
                wrote += 1
            except Exception:
                pass
        audio.save()
        return True, f"tagged ({wrote} field(s))"
    except Exception as e:  # noqa: BLE001 - bulk tagging must never crash the loop
        return False, str(e)[:120]


def apply_template_to_files(paths, template, progress_cb=None):
    ok = 0
    for i, p in enumerate(paths):
        good, _msg = apply_template(p, template)
        ok += 1 if good else 0
        if progress_cb:
            progress_cb(i + 1, len(paths), p, good)
    return ok
