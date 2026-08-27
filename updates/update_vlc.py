"""Standalone updater for VLC Media Player. Downloads and silently runs the
official installer from videolan.org."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.dependencies import install_vlc

def run():
    return install_vlc(progress_callback=print)

if __name__ == "__main__":
    ok, msg = run()
    print(msg)
