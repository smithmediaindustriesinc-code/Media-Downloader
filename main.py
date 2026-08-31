import sys

if __name__ == "__main__":
    # A frozen (PyInstaller) build has no standalone python.exe on the
    # end user's machine to spawn separately - so the background queue
    # daemon (see core/queue_daemon.py and gui/app.py's close handler)
    # is invoked through this SAME executable, with a command-line flag
    # that skips the GUI entirely and runs headless instead. Checked
    # before importing gui.app, since that import pulls in customtkinter/
    # Tk - unnecessary weight for a process that's never going to show a
    # window.
    if "--daemon" in sys.argv:
        from core.queue_daemon import process_pending_queue
        process_pending_queue()
        sys.exit(0)

    # Kept as a harmless no-op: older installers still pass this flag on
    # upgrade. The page scraper switched to yt-dlp's generic extractor in
    # 1.7.3 and no longer needs a bundled browser.
    if "--playwright-install" in sys.argv:
        print("[playwright-install] Not needed - the page scraper no longer uses a browser.")
        sys.exit(0)

    from gui.app import run
    run()
