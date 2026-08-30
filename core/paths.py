import os
import sys


def _entry_script_dir():
    """The folder main.py itself lives in - i.e. wherever the person put
    the app, regardless of what the current working directory happens to
    be when they launch it. Every relative path in this app (config,
    history, playlists, downloaded ffmpeg, bundled resources) is anchored
    to this, not to os.getcwd(), so it doesn't matter whether the app is
    launched by double-click, from a shortcut, from a different folder in
    a terminal, or with a working directory set some other way."""
    main_module = sys.modules.get("__main__")
    if main_module is not None and hasattr(main_module, "__file__") and main_module.__file__:
        return os.path.dirname(os.path.abspath(main_module.__file__))
    # Fallback (e.g. running in an interactive shell with no __main__.__file__):
    # core/paths.py's own location is two levels below the project root.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def instance_name():
    """The app instance's display name - normally "Media Downloader".

    A "beta instance" build (installed side-by-side with the stable copy by
    the in-app updater's "install the beta as a separate copy" option) is
    produced by compiling installer.iss with /DBETA; that installer drops an
    `instance.flag` file next to the .exe whose contents are "beta". When
    that flag is present this returns "Media Downloader Beta", so the beta
    keeps its own %APPDATA%\\Media Downloader Beta folder and window title
    and can't disturb the stable install's settings/history.

    Running from source (no flag) it is always "Media Downloader"."""
    try:
        flag = os.path.join(install_dir(), "instance.flag")
        if os.path.isfile(flag):
            with open(flag, "r", encoding="utf-8", errors="ignore") as fh:
                if fh.read().strip().lower() == "beta":
                    return "Media Downloader Beta"
    except Exception:
        pass
    return "Media Downloader"


def app_dir():
    """Folder where writable app data (config, history, playlists, ffmpeg/)
    lives.
    - Normal run (python main.py): wherever main.py is - see
      _entry_script_dir(). Works no matter what folder you run the command
      from.
    - Frozen .exe on Windows: %APPDATA%\\<instance_name()> (normally
      "Media Downloader"; "Media Downloader Beta" for a beta instance). The
      .exe itself often lives in C:\\Program Files\\..., which regular
      (non-admin) users can't write to - saving there causes a silent
      PermissionError. Per-user AppData is where Windows apps are supposed
      to keep this kind of data.
    - Frozen .exe elsewhere (Mac/Linux): folder the executable sits in."""
    if getattr(sys, "frozen", False):
        if os.name == "nt":
            base = os.environ.get("APPDATA") or os.path.expanduser("~")
            path = os.path.join(base, instance_name())
            os.makedirs(path, exist_ok=True)
            return path
        return os.path.dirname(sys.executable)
    return _entry_script_dir()


def resource_path(filename):
    """Folder for bundled read-only resources (DISCLAIMER.txt, assets/, icon.ico)."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", app_dir())
    else:
        base = _entry_script_dir()
    return os.path.join(base, filename)


def install_dir():
    """The folder the actual .exe/installed files live in. This is
    DIFFERENT from app_dir() for a frozen Windows build - app_dir() was
    deliberately moved to %APPDATA% so the app can write config/history
    without admin rights, even though the .exe itself typically lives in
    C:\\Program Files\\.... install_dir() is for the rare cases that need
    the real install location instead, like finding Inno Setup's
    uninstaller (unins000.exe), which lives next to the .exe, not in
    %APPDATA%.
    - Frozen: folder containing sys.executable.
    - Not frozen (running from source): same as app_dir()."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return _entry_script_dir()


def ensure_media_folders(download_root):
    """Create Videos/ and Music/ under the chosen default download folder.
    Returns (video_path, music_path)."""
    video_path = os.path.join(download_root, "Videos")
    music_path = os.path.join(download_root, "Music")
    os.makedirs(video_path, exist_ok=True)
    os.makedirs(music_path, exist_ok=True)
    return video_path, music_path


def ensure_playlists_folder(download_root):
    """Create Playlists/ under the chosen default download folder. This is
    where each individual playlist gets its own subfolder (see
    gui/app.py's _playlist_folder). Returns the Playlists root path."""
    playlists_path = os.path.join(download_root, "Playlists")
    os.makedirs(playlists_path, exist_ok=True)
    return playlists_path


def ensure_archived_content_folder(download_root):
    """Create "Archived Content"/ under the chosen default download
    folder - where a deleted playlist's files land if the user chooses
    to archive rather than delete them (see gui/app.py's playlist
    deletion destination dialog), and where the Media Library's
    per-item Archive button moves files to. Returns the folder's path."""
    archived_path = os.path.join(download_root, "Archived Content")
    os.makedirs(archived_path, exist_ok=True)
    return archived_path
