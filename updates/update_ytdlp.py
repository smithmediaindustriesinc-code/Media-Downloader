"""Standalone updater for yt-dlp. Run directly or call run() from the app."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.dependencies import install_python_package, PIP_PACKAGES

def run():
    return install_python_package(PIP_PACKAGES["yt_dlp"])

if __name__ == "__main__":
    ok, msg = run()
    print(msg)
