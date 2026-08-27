"""
A standalone, GUI-free process that finishes whatever's still queued in
download_requests.json - this is what "continue downloading in the
background when the app is closed" actually is: not a magic
always-on service, but a lightweight worker that picks up exactly where
the GUI left off and exits once the queue is empty.

Deliberately imports nothing from gui/ (no customtkinter/Tk) so it can
run headless - spawned as a detached background process from
gui/app.py's close handler when Settings > Advanced has "Continue
downloads in background" enabled and there's still pending work, or
invoked directly via `python -m core.queue_daemon` / the frozen build's
own entry point for the same purpose.

This also doubles as the "basic interface for other apps" groundwork
that was asked about: any external process can add work by writing a
request into download_requests.json in the same shape start_request()
already produces (or, more simply, by using this module's own
add_external_request() below) and this daemon will pick it up next time
it runs - no special protocol beyond the JSON file format the app
already uses for itself.
"""
import os
import sys
import time

from core.config import load_config
from core.download_requests import get_all_requests, update_item, finish_request, start_request, reset_stalled_downloads
from core.downloader import Downloader, DownloadCancelled, DownloadStageError, download_with_retry, fetch_media_info
from core.history import add_entry, update_entry
from core.utils import make_unique_name, sanitize_filename, beautify_title


def _log(message):
    # No GUI to log into - stdout is enough for a background process;
    # redirected to a file by whoever launches it if that's wanted.
    print(f"[queue_daemon] {message}", flush=True)


def add_external_request(dtype, urls, custom_name=None):
    """The simplest possible "interface for other apps": call this (or
    just write the equivalent JSON directly) to add work for the daemon
    to pick up on its next pass, without needing the GUI running at
    all. Returns the request_id."""
    mode = "queue" if len(urls) > 1 else "single"
    return start_request(dtype, mode, urls, custom_name=custom_name)


def process_pending_queue(max_idle_polls=0, poll_interval_s=2):
    """Processes every 'pending' item across every in-progress request,
    one at a time (same reasoning as the GUI's own batch delay - not
    hammering a site with simultaneous requests). Once nothing pending
    is left, exits (max_idle_polls=0, the default - a "finish what's
    queued and stop" run) unless max_idle_polls > 0, in which case it
    polls that many extra times at poll_interval_s apart before giving
    up, in case new work gets added while it's running (e.g. by another
    process using add_external_request() concurrently).

    Returns the number of items actually processed."""
    cfg = load_config()
    recovered = reset_stalled_downloads("pending")
    if recovered:
        _log(f"Recovered {recovered} interrupted download(s) from a previous run - resuming.")
    processed = 0
    idle_polls = 0

    while True:
        in_progress, _completed = get_all_requests()
        pending_work = []
        for req in in_progress:
            for url, item in req["items"].items():
                if item.get("status") == "pending":
                    pending_work.append((req, url))

        if not pending_work:
            if idle_polls >= max_idle_polls:
                break
            idle_polls += 1
            time.sleep(poll_interval_s)
            continue
        idle_polls = 0

        req, url = pending_work[0]
        request_id = req["request_id"]
        dtype = req["dtype"]
        out_dir = req.get("out_dir") or cfg.get("download_root") or cfg.get("video_path") or cfg.get("music_path")
        if not out_dir:
            _log(f"No output folder configured - skipping {url}")
            update_item(request_id, url, status="failed", error="No output folder configured")
            continue

        update_item(request_id, url, status="downloading")
        _log(f"Downloading: {url}")
        history_id = add_entry(url, url, dtype, "", "Analyzing")
        media_info = None
        try:
            media_info = fetch_media_info(url)
            name = sanitize_filename(beautify_title(media_info.get("title", "download")))
        except Exception:
            name = "download"
        ext = cfg["video_format"] if dtype == "Video" else cfg["audio_format"]
        unique_name = make_unique_name(out_dir, name, ext)
        update_item(request_id, url, name=unique_name)
        update_entry(history_id, name=unique_name, status="In Progress")

        downloader = Downloader()
        status, path = "Success", ""
        try:
            if dtype == "Video":
                path = download_with_retry(
                    downloader.download_video, url=url, name=unique_name, out_dir=out_dir,
                    quality_key=cfg["video_quality"], fmt=cfg["video_format"], playlist=False,
                    subtitles=cfg.get("default_subtitles", False), aspect_ratio=cfg.get("aspect_ratio", "Any"),
                    cookies_from_browser=cfg.get("cookies_from_browser", "none"),
                    prefetched_info=media_info
                )
            else:
                path = download_with_retry(
                    downloader.download_audio, url=url, name=unique_name, out_dir=out_dir,
                    quality=cfg["audio_quality"], fmt=cfg["audio_format"], playlist=False,
                    embed_thumbnail=cfg.get("embed_thumbnail", True),
                    cookies_from_browser=cfg.get("cookies_from_browser", "none")
                )
            update_item(request_id, url, status="success", path=path,
                        elapsed_seconds=downloader.elapsed_seconds())
            _log(f"Done: {path}")
        except DownloadCancelled:
            status = "Cancelled"
            update_item(request_id, url, status="failed", error="Cancelled")
        except DownloadStageError as e:
            status = "Failed"
            update_item(request_id, url, status="failed", error=f"{e.stage}: {e.original}")
            _log(f"Failed: {e}")
        except Exception as e:
            status = "Failed"
            update_item(request_id, url, status="failed", error=str(e))
            _log(f"Unexpected error: {e}")

        update_entry(history_id, name=unique_name, path=path, status=status)
        processed += 1

        # Once every item in this request is resolved (nothing left
        # pending/downloading), finish it - matches how the GUI itself
        # decides a request is done.
        remaining = [i for i in req["items"].values() if i.get("status") in ("pending", "downloading")]
        current_state = get_all_requests()[0]
        this_req = next((r for r in current_state if r["request_id"] == request_id), None)
        if this_req:
            still_open = any(i.get("status") in ("pending", "downloading") for i in this_req["items"].values())
            if not still_open:
                finish_request(request_id)

    return processed


if __name__ == "__main__":
    count = process_pending_queue()
    _log(f"Finished - processed {count} item(s).")
    sys.exit(0)
