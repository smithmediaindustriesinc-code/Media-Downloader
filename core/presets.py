"""F1 (1.7.4): Download presets - named bundles of the download settings the
user changes most often, so a one-click dropdown swaps between common setups
("YouTube 1080p + subs", "Spotify to MP3 320k", ...).

Presets live in config as cfg["download_presets"] = [{"name", "settings": {...}}].
`settings` only holds the keys in PRESET_KEYS. The GUI captures the current
Download-tab state into a preset and applies a chosen preset back onto both
config and the live widgets (see gui/app.py).
"""

# The download parameters a preset carries. type_var is "Video"/"Audio".
PRESET_KEYS = (
    "download_type",       # "Video" | "Audio"  (the Download-tab type selector)
    "video_quality",
    "video_format",
    "aspect_ratio",
    "audio_quality",
    "audio_format",
    "default_subtitles",
    "default_playlist",
    "embed_thumbnail",
)


def _norm_name(name):
    return (name or "").strip()


def list_presets(cfg):
    out = []
    for p in cfg.get("download_presets") or []:
        if isinstance(p, dict) and _norm_name(p.get("name")):
            out.append(p)
    return out


def preset_names(cfg):
    return [p["name"] for p in list_presets(cfg)]


def get_preset(cfg, name):
    name = _norm_name(name)
    for p in list_presets(cfg):
        if p["name"] == name:
            return p
    return None


def make_preset(name, settings):
    """Build a preset dict from a name + a settings dict (only PRESET_KEYS are
    kept)."""
    return {"name": _norm_name(name),
            "settings": {k: settings[k] for k in PRESET_KEYS if k in settings}}


def save_preset(cfg, preset):
    """Add or replace a preset by name. Mutates cfg['download_presets'].
    Returns the stored preset."""
    name = _norm_name(preset.get("name"))
    if not name:
        raise ValueError("A preset needs a name.")
    preset = {"name": name,
              "settings": {k: v for k, v in (preset.get("settings") or {}).items()
                           if k in PRESET_KEYS}}
    presets = [p for p in (cfg.get("download_presets") or [])
               if _norm_name(p.get("name")) != name]
    presets.append(preset)
    presets.sort(key=lambda p: p["name"].lower())
    cfg["download_presets"] = presets
    return preset


def delete_preset(cfg, name):
    name = _norm_name(name)
    before = len(cfg.get("download_presets") or [])
    cfg["download_presets"] = [p for p in (cfg.get("download_presets") or [])
                               if _norm_name(p.get("name")) != name]
    return len(cfg["download_presets"]) != before
