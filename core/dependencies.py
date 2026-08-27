"""
Dependency detection + installation.

Handles three kinds of dependency:
  - PYTHON packages (customtkinter, yt-dlp, pillow) -> checked via import,
    installed via `pip install`.
  - FFMPEG -> not on PATH by default on most Windows machines. We download
    a static build and drop ffmpeg.exe/ffprobe.exe into <app_dir>/ffmpeg/bin
    so nothing needs admin rights or a PATH edit. yt-dlp is then told where
    to find it via ffmpeg_location.
  - VLC -> checked via common install paths / Windows registry. Installed
    via VLC's official silent installer switches.

Every check/install function returns (ok: bool, message: str) so the GUI can
show the user exactly what happened instead of failing silently.
"""
import importlib
import importlib.metadata
import io
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

from core.paths import app_dir

FFMPEG_DIR = os.path.join(app_dir(), "ffmpeg", "bin")
FFMPEG_EXE = os.path.join(FFMPEG_DIR, "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg")
FFPROBE_EXE = os.path.join(FFMPEG_DIR, "ffprobe.exe" if platform.system() == "Windows" else "ffprobe")

FFMPEG_DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

WINDOWS_VLC_PATHS = [
    r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
]
VLC_DOWNLOAD_URL = "https://get.videolan.org/vlc/last/win64/vlc-win64.exe"

PIP_PACKAGES = {
    "customtkinter": "customtkinter>=5.2.0",
    "yt_dlp": "yt-dlp>=2025.1.1",
    "PIL": "pillow>=10.0.0",
}
IMPORT_NAMES = {
    "customtkinter": "customtkinter",
    "yt_dlp": "yt_dlp",
    "PIL": "PIL",
}
DIST_NAMES = {
    "customtkinter": "customtkinter",
    "yt_dlp": "yt-dlp",
    "PIL": "Pillow",
}


# --------------------------------------------------------------------- #
# Python packages
# --------------------------------------------------------------------- #
def check_python_package(import_name):
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False


def installed_version(dist_name):
    try:
        return importlib.metadata.version(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def install_python_package(pip_spec):
    """pip install --upgrade <spec>. Only meaningful when running from
    source - a frozen .exe has no live Python env to modify."""
    if getattr(sys, "frozen", False):
        return False, ("This is a packaged .exe - Python packages are bundled in and can't "
                        "be updated in place. Update from source and rebuild the .exe instead.")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", pip_spec],
            capture_output=True, text=True, timeout=180
        )
        ok = result.returncode == 0
        return ok, (result.stdout + result.stderr)[-600:] if not ok else f"{pip_spec} is up to date."
    except Exception as e:
        return False, f"Update failed: {e}"


# --------------------------------------------------------------------- #
# FFmpeg
# --------------------------------------------------------------------- #
def check_ffmpeg():
    """Returns (ok, path_or_None). Checks three places, in order: FFmpeg
    already downloaded by this app to app_dir() at runtime (the existing
    behavior), FFmpeg bundled directly into the installer next to the
    app's own executable (see install_dir() and installer.iss's
    vendor\\ffmpeg [Files] entry - avoids needing every user to download
    it separately on first run), then finally the system PATH."""
    if os.path.exists(FFMPEG_EXE):
        return True, FFMPEG_EXE
    from core.paths import install_dir
    bundled = os.path.join(install_dir(), "ffmpeg", "bin",
                            "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg")
    if os.path.exists(bundled):
        return True, bundled
    found = shutil.which("ffmpeg")
    if found:
        return True, found
    return False, None


def install_ffmpeg(progress_callback=None):
    def log(msg):
        if progress_callback:
            progress_callback(msg)

    if platform.system() != "Windows":
        return False, ("Automatic FFmpeg install is only implemented for Windows. "
                        "Install ffmpeg via your package manager (apt/brew) instead.")

    log("Downloading FFmpeg (this can take a minute)...")
    try:
        with urllib.request.urlopen(FFMPEG_DOWNLOAD_URL, timeout=60) as resp:
            data = resp.read()
    except Exception as e:
        return False, f"FFmpeg download failed: {e}"

    log("Extracting FFmpeg...")
    os.makedirs(FFMPEG_DIR, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for member in z.namelist():
                if member.endswith("bin/ffmpeg.exe") or member.endswith("bin/ffprobe.exe"):
                    target = os.path.join(FFMPEG_DIR, os.path.basename(member))
                    with z.open(member) as src, open(target, "wb") as dst:
                        dst.write(src.read())
    except Exception as e:
        return False, f"FFmpeg extraction failed: {e}"

    ok, path = check_ffmpeg()
    if ok:
        return True, f"FFmpeg installed to {FFMPEG_DIR}"
    return False, "FFmpeg extraction completed but the executable wasn't found afterward."


# --------------------------------------------------------------------- #
# VLC
# --------------------------------------------------------------------- #
def find_vlc():
    on_path = shutil.which("vlc")
    if on_path:
        return on_path
    if platform.system() == "Windows":
        for p in WINDOWS_VLC_PATHS:
            if os.path.exists(p):
                return p
    return None


def check_vlc():
    path = find_vlc()
    return (path is not None), path


def install_vlc(progress_callback=None):
    def log(msg):
        if progress_callback:
            progress_callback(msg)

    if platform.system() != "Windows":
        return False, "Automatic VLC install is only implemented for Windows. Get it from videolan.org."

    log("Downloading VLC installer...")
    last_error = None
    data = None
    # get.videolan.org occasionally returns a transient server error (HTTP
    # 500) rather than the file - retry a couple of times before giving up,
    # since it's usually momentary rather than a real outage.
    for attempt in range(3):
        try:
            with urllib.request.urlopen(VLC_DOWNLOAD_URL, timeout=90) as resp:
                data = resp.read()
            break
        except Exception as e:
            last_error = e
            if attempt < 2:
                log(f"Download attempt {attempt + 1} failed ({e}), retrying...")
                time.sleep(2)
    if data is None:
        return False, (
            f"VLC download failed after 3 attempts: {last_error}\n\n"
            "This is usually a temporary problem on VideoLAN's download "
            "server, not something wrong with this app - try again in a "
            "few minutes, or download and install it yourself from "
            "https://www.videolan.org/vlc/"
        )

    installer_path = os.path.join(app_dir(), "vlc_setup_temp.exe")
    try:
        with open(installer_path, "wb") as f:
            f.write(data)
    except Exception as e:
        return False, f"Could not save VLC installer: {e}"

    log("Installing VLC quietly (this can take a minute)...")
    try:
        subprocess.run([installer_path, "/L=1033", "/S"], timeout=300)
    except Exception as e:
        return False, f"VLC install failed: {e}"
    finally:
        try:
            os.remove(installer_path)
        except OSError:
            pass

    if find_vlc():
        return True, "VLC installed successfully."
    return False, "Installer ran but VLC still wasn't found. It may need a manual install from videolan.org."


# --------------------------------------------------------------------- #
# Aggregate status for the Version tab
# --------------------------------------------------------------------- #
def check_all():
    results = []

    for key, spec in PIP_PACKAGES.items():
        installed = check_python_package(IMPORT_NAMES[key])
        version = installed_version(DIST_NAMES[key]) if installed else None
        detail = f"Installed: v{version}" if installed else "Not installed"
        results.append({
            "name": spec.split(">=")[0].split("==")[0],
            "kind": "python",
            "pip_spec": spec,
            "ok": installed,
            "detail": detail,
        })

    ffmpeg_ok, ffmpeg_path = check_ffmpeg()
    results.append({
        "name": "FFmpeg",
        "kind": "ffmpeg",
        "pip_spec": None,
        "ok": ffmpeg_ok,
        "detail": f"Found: {ffmpeg_path}" if ffmpeg_ok else "Not installed (required for merging/audio extraction)",
    })

    vlc_ok, vlc_path = check_vlc()
    results.append({
        "name": "VLC Media Player",
        "kind": "vlc",
        "pip_spec": None,
        "ok": vlc_ok,
        "detail": f"Found: {vlc_path}" if vlc_ok else "Not installed (optional, used by 'Open in VLC')",
    })

    return results
