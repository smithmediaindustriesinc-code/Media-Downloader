"""F14 (1.7.4): convert an already-downloaded file to another container /
codec with the app's own bundled FFmpeg - no re-download.

convert(src, target_ext, audio_bitrate=None) -> (ok, out_path, message).
Never raises. Runs FFmpeg with a hidden window.
"""
import os
import subprocess

from core.dependencies import check_ffmpeg

AUDIO_TARGETS = ["mp3", "m4a", "flac", "wav", "ogg", "opus", "aac"]
VIDEO_TARGETS = ["mp4", "mkv", "webm", "mov"]
ALL_TARGETS = AUDIO_TARGETS + VIDEO_TARGETS

_AUDIO_CODEC = {
    "mp3": ["-c:a", "libmp3lame"],
    "m4a": ["-c:a", "aac"],
    "aac": ["-c:a", "aac"],
    "flac": ["-c:a", "flac"],
    "wav": ["-c:a", "pcm_s16le"],
    "ogg": ["-c:a", "libvorbis"],
    "opus": ["-c:a", "libopus"],
}


def _ffmpeg():
    ok, path = check_ffmpeg()
    return path if ok else None


def convert(src, target_ext, audio_bitrate=None, log_cb=None):
    def log(m):
        if log_cb:
            log_cb(m)

    if not src or not os.path.isfile(src):
        return False, "", "source file not found"
    target_ext = target_ext.lower().lstrip(".")
    if target_ext not in ALL_TARGETS:
        return False, "", f"unsupported target: {target_ext}"
    ff = _ffmpeg()
    if not ff:
        return False, "", "FFmpeg isn't installed (Version tab)."

    stem, cur_ext = os.path.splitext(src)
    if cur_ext.lower().lstrip(".") == target_ext:
        return False, "", "already that format"
    out = f"{stem}.{target_ext}"
    n = 1
    while os.path.exists(out):
        out = f"{stem} ({n}).{target_ext}"
        n += 1

    cmd = [ff, "-y", "-i", src]
    if target_ext in AUDIO_TARGETS:
        cmd += ["-vn"]
        cmd += _AUDIO_CODEC.get(target_ext, [])
        if audio_bitrate and target_ext in ("mp3", "m4a", "aac", "ogg", "opus"):
            cmd += ["-b:a", f"{int(audio_bitrate)}k"]
    else:
        # container change - copy streams when we can, re-encode video to
        # H.264 for mp4/mov which won't accept arbitrary codecs.
        if target_ext in ("mp4", "mov"):
            cmd += ["-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart"]
        else:
            cmd += ["-c", "copy"]
    cmd += [out]

    log(f"Converting -> {os.path.basename(out)} ...")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                           timeout=3600)
    except Exception as e:  # noqa: BLE001
        return False, "", f"FFmpeg failed to run: {e}"
    if r.returncode != 0 or not os.path.isfile(out):
        tail = (r.stderr or "")[-300:]
        return False, "", f"conversion failed: {tail}"
    return True, out, "converted"


def convert_many(paths, target_ext, audio_bitrate=None, progress_cb=None, log_cb=None):
    done = []
    for i, p in enumerate(paths):
        ok, out, msg = convert(p, target_ext, audio_bitrate, log_cb=log_cb)
        if ok:
            done.append(out)
        if progress_cb:
            progress_cb(i + 1, len(paths), p, ok, msg)
    return done
