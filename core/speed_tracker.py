"""
Tracks recent download-speed samples for a smoothed, outlier-resistant
displayed speed and ETA - rather than directly showing yt-dlp's own raw,
often jittery instant speed reading (which can swing wildly between one
progress-hook call and the next, especially with parallel fragment
downloads).

Sampling: add_sample() is meant to be called from the download progress
hook on every callback, but only actually RECORDS a new sample if at
least SAMPLE_INTERVAL_S (0.5s) has passed since the last one - this
decouples the recorded sample rate from however often the hook actually
fires (which varies a lot with chunk size/network conditions), per how
this was specifically asked for ("measure the download speed every
0.5s").

Outlier rejection: a new sample is rejected if it deviates from the
recent samples' median by more than a tolerance that scales with the
connection's measured ping - a higher-latency connection naturally has
more throughput variance moment to moment, so it gets a looser
tolerance; a low-latency one gets tighter, since a wild swing on a
fast/stable connection is more likely a genuine glitch than real
variance. This is the "use an average ping" outlier criterion asked
for - core/network_status.py already measures this periodically for
the app's own connection-quality indicator, so this reuses that same
measurement rather than pinging separately.

Display: get_average(window_seconds=5) returns the average of whatever
samples fall within the last N seconds - both the displayed speed AND
what ETA is computed from are meant to come from this, refreshed once a
second by the caller (see gui/app.py's speed-display tick), not
recomputed on every raw hook call.
"""
import statistics
import time

SAMPLE_INTERVAL_S = 0.5
BASE_TOLERANCE = 0.5   # 50% deviation from the recent median tolerated at 0ms ping
PING_SCALE_MS = 200    # every extra PING_SCALE_MS of ping adds another BASE_TOLERANCE of slack
DEFAULT_PING_MS = 100  # used when no ping measurement is available yet


class SpeedTracker:
    def __init__(self):
        self._samples = []  # list of (timestamp, speed_bytes_per_sec), oldest first
        self._last_sample_time = None  # None, not 0.0 - a real sample timestamp of
                                        # exactly 0.0 would otherwise collide with this
                                        # "no samples yet" sentinel and get wrongly
                                        # rejected by the cadence check below.

    def reset(self):
        """Called at the start of each new download - a fresh item
        shouldn't have its early samples judged against (or averaged
        with) an entirely different, unrelated download's speed
        history."""
        self._samples = []
        self._last_sample_time = None

    def add_sample(self, speed, ping_ms=None, now=None):
        """speed: bytes/sec: non-positive or None values are ignored
        outright (yt-dlp reports None/0 speed between fragments,
        wrapping up, etc - not real, useful signal). ping_ms: the most
        recently measured connection latency (None uses a moderate
        default tolerance instead of rejecting nothing/everything)."""
        if not speed or speed <= 0:
            return
        now = now if now is not None else time.time()
        if self._last_sample_time is not None and now - self._last_sample_time < SAMPLE_INTERVAL_S:
            return
        if self._is_outlier(speed, ping_ms):
            return
        self._samples.append((now, speed))
        self._last_sample_time = now
        cutoff = now - 30  # no need to keep samples from minutes ago around forever
        self._samples = [(t, s) for t, s in self._samples if t >= cutoff]

    def _is_outlier(self, speed, ping_ms):
        if len(self._samples) < 3:
            return False  # not enough history yet to judge against - let it in
        recent_speeds = [s for _, s in self._samples[-10:]]
        median = statistics.median(recent_speeds)
        if median <= 0:
            return False
        ping = ping_ms if ping_ms is not None else DEFAULT_PING_MS
        tolerance = BASE_TOLERANCE + (ping / PING_SCALE_MS) * BASE_TOLERANCE
        deviation = abs(speed - median) / median
        return deviation > tolerance

    def get_average(self, window_seconds=5, now=None):
        """The rolling average used for both display and ETA - None if
        there's nothing in the window yet (a caller should fall back to
        whatever it had before, or show nothing, rather than treating
        None as a literal 0 speed)."""
        now = now if now is not None else time.time()
        cutoff = now - window_seconds
        in_window = [s for t, s in self._samples if t >= cutoff]
        if not in_window:
            return None
        return sum(in_window) / len(in_window)

    def sample_count(self):
        return len(self._samples)
