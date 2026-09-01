"""F11 / F16 (1.7.4): aggregate stats over the existing history.json - no new
data collection. Used by the Stats sub-tab and the monthly bandwidth budget.

Byte totals prefer a 'bytes' field stored on the history entry at download
time (see gui/app.py), falling back to os.path.getsize() of the file if it's
still on disk.
"""
import datetime
import os

from core.history import load_history


def _entry_bytes(e):
    b = e.get("bytes")
    if isinstance(b, (int, float)) and b > 0:
        return int(b)
    p = e.get("path") or ""
    try:
        if p and os.path.isfile(p):
            return os.path.getsize(p)
    except OSError:
        pass
    return 0


def _parse_date(e):
    try:
        return datetime.datetime.strptime(e.get("date", "")[:16], "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return None


def bytes_this_month(now=None):
    now = now or datetime.datetime.now()
    total = 0
    for e in load_history():
        if e.get("status") not in ("Success",):
            continue
        d = _parse_date(e)
        if d and d.year == now.year and d.month == now.month:
            total += _entry_bytes(e)
    return total


def summarize():
    """A dict of headline numbers for the Stats sub-tab."""
    hist = load_history()
    now = datetime.datetime.now()
    total_items = len(hist)
    success = sum(1 for e in hist if e.get("status") == "Success")
    failed = sum(1 for e in hist if e.get("status") == "Failed")
    total_bytes = 0
    month_bytes = 0
    by_type = {}
    by_ext = {}
    by_source = {}
    per_week = {}
    for e in hist:
        b = _entry_bytes(e)
        total_bytes += b
        d = _parse_date(e)
        if d and d.year == now.year and d.month == now.month and e.get("status") == "Success":
            month_bytes += b
        t = e.get("type") or "?"
        by_type[t] = by_type.get(t, 0) + 1
        ext = os.path.splitext(e.get("path") or "")[1].lstrip(".").lower() or "?"
        by_ext[ext] = by_ext.get(ext, 0) + b
        src = _source_of(e.get("url") or "")
        by_source[src] = by_source.get(src, 0) + 1
        if d:
            wk = d.strftime("%Y-W%W")
            per_week[wk] = per_week.get(wk, 0) + 1
    return {
        "total_items": total_items,
        "success": success,
        "failed": failed,
        "success_rate": (success / total_items) if total_items else 0.0,
        "total_bytes": total_bytes,
        "month_bytes": month_bytes,
        "by_type": by_type,
        "by_ext": dict(sorted(by_ext.items(), key=lambda kv: kv[1], reverse=True)[:8]),
        "by_source": dict(sorted(by_source.items(), key=lambda kv: kv[1], reverse=True)[:8]),
        "per_week": dict(sorted(per_week.items())[-8:]),
    }


def _source_of(url):
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        host = host.lower().removeprefix("www.").removeprefix("m.")
        return host or "?"
    except Exception:
        return "?"


def human_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
