"""Standalone updater for FFmpeg. Downloads the latest static build into
<app_dir>/ffmpeg/bin - no admin rights or PATH edit required."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.dependencies import install_ffmpeg

def run():
    return install_ffmpeg(progress_callback=print)

if __name__ == "__main__":
    ok, msg = run()
    print(msg)
