"""Runs every updater in this folder in sequence and prints a summary.
This is the script the app's 'Update All' button effectively mirrors."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import dependencies as deps

def run():
    summary = []
    for item in deps.check_all():
        if item["kind"] == "python":
            ok, msg = deps.install_python_package(item["pip_spec"])
        elif item["kind"] == "ffmpeg":
            ok, msg = deps.install_ffmpeg(progress_callback=print)
        else:
            ok, msg = False, "Unknown dependency."
        summary.append((item["name"], ok, msg))
    return summary

if __name__ == "__main__":
    for name, ok, msg in run():
        print(f"{name}: {'OK' if ok else 'FAILED'} - {msg}")
