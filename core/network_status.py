"""
Internet connectivity checking for the top-right status indicator.

Measures latency to a fast, reliable endpoint via a lightweight HTTPS
HEAD-ish request (urllib, no new dependency), buckets it into a quality
tier, and reports both the tier and the raw ping in ms so the UI can
show text like "42ms - Good".

Tiers (ms):
  Good  : < 100
  Poor  : 100-300
  Bad   : > 300
  None  : request failed entirely / no connectivity
"""
import socket
import time
import urllib.request

CHECK_HOST = "https://www.google.com/generate_204"
TIMEOUT = 3.0

GOOD_MAX_MS = 100
POOR_MAX_MS = 300


def check_connection():
    """Returns (tier, ping_ms) where tier is one of
    'good' | 'poor' | 'bad' | 'none', and ping_ms is a float or None."""
    # urlopen's own timeout= doesn't reliably bound DNS resolution time on
    # every platform - a genuinely unreachable host can hang well past
    # TIMEOUT while resolving before urlopen's timeout ever kicks in.
    # socket.setdefaulttimeout() is a harder backstop that also covers
    # the resolution step, so a real "no route to host" situation fails
    # fast instead of hanging this background check indefinitely.
    old_default = socket.getdefaulttimeout()
    socket.setdefaulttimeout(TIMEOUT)
    start = time.monotonic()
    try:
        req = urllib.request.Request(CHECK_HOST, method="HEAD")
        with urllib.request.urlopen(req, timeout=TIMEOUT):
            pass
        ping_ms = (time.monotonic() - start) * 1000
    except Exception:
        return "none", None
    finally:
        socket.setdefaulttimeout(old_default)

    if ping_ms < GOOD_MAX_MS:
        return "good", ping_ms
    elif ping_ms < POOR_MAX_MS:
        return "poor", ping_ms
    else:
        return "bad", ping_ms


TIER_LABELS = {"good": "Good", "poor": "Poor", "bad": "Bad", "none": "No Internet"}
TIER_COLORS = {"good": "#2fa84f", "poor": "#e0b400", "bad": "#c0392b", "none": "#888888"}
TIER_ICONS = {"good": "full.png", "poor": "poor.png", "bad": "bad.png", "none": "none.png"}
