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

    # Run once, automatically, by installer.iss's [Run] section right
    # after installation - downloads the actual Chromium browser
    # Playwright needs (pip-installing the playwright PACKAGE alone
    # never does this; it's a separate step) so URL Scraping works
    # immediately, rather than silently failing to scrape the first
    # time a user tries it with no clear explanation why.
    if "--playwright-install" in sys.argv:
        from core.url_scraper import ensure_playwright_browser_installed
        ok, message = ensure_playwright_browser_installed()
        print(f"[playwright-install] {message}")
        sys.exit(0 if ok else 1)

    from gui.app import run
    run()
