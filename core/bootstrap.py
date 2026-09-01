"""One-time startup wiring: crash logging, startup heartbeat logging, and
faulthandler. Called from gui/app.py's run() so main.py itself can stay
minimal - its only real job is running the app's mainloop."""
import os
import sys


def bootstrap():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from core.crash_log import install_global_excepthook
    from core.startup_log import reset, mark

    reset()
    mark("crash logging + startup logging initialized")

    install_global_excepthook()
    mark("global excepthook installed")

    # faulthandler can catch some low-level (segfault-style) crashes that
    # bypass Python's normal exception handling entirely - cheap safety net.
    try:
        import faulthandler
        from core.paths import app_dir
        fault_log_path = os.path.join(app_dir(), "fault_log.txt")
        fault_log = open(fault_log_path, "w")
        faulthandler.enable(file=fault_log, all_threads=True)
        mark("faulthandler enabled")
    except Exception as e:
        mark(f"faulthandler setup failed (non-fatal): {e}")

    # Smooth scrolling everywhere - must happen before App() is
    # constructed (any CTkScrollableFrame instances made after this point
    # get it automatically), which is exactly when this runs.
    try:
        from gui.smooth_scroll import patch_smooth_scrolling
        patch_smooth_scrolling()
        mark("smooth scrolling patched")
    except Exception as e:
        mark(f"smooth scrolling patch failed (non-fatal): {e}")

    # Apply the user's saved Accessibility > Scroll Speed setting - after
    # patch_smooth_scrolling() so the mechanism it configures already
    # exists, but still before App() so the very first scroll anywhere
    # already reflects it, not just ones after a settings visit.
    try:
        from gui.smooth_scroll import set_scroll_speed
        from core.config import load_config
        set_scroll_speed(load_config().get("scroll_speed_ms", 8))
        mark("scroll speed applied from saved settings")
    except Exception as e:
        mark(f"scroll speed setting failed (non-fatal): {e}")

    # Sliders shouldn't respond to a stray mouse-wheel scroll - same
    # "patch the class before any instances exist" approach as above.
    try:
        from gui.widget_patches import disable_slider_mousewheel
        disable_slider_mousewheel()
        mark("slider mousewheel disabled")
    except Exception as e:
        mark(f"slider mousewheel patch failed (non-fatal): {e}")

    # F2 (1.7.4): apply the saved download speed cap before any download runs.
    try:
        from core.downloader import set_rate_limit
        from core.config import load_config
        _cfg = load_config()
        set_rate_limit(_cfg.get("speed_limit_kbps", 0) * 1024
                       if _cfg.get("speed_limit_enabled") else 0)
        mark("download speed limit applied from saved settings")
    except Exception as e:
        mark(f"speed limit setting failed (non-fatal): {e}")
