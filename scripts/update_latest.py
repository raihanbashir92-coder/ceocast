#!/usr/bin/env python3
"""Update latest.json and thumb.jpg with the newest long-form CEOCAST video.

Reads the channel's YouTube RSS feed, skips Shorts, and only writes files
when the newest video differs from latest.json (or thumb.jpg is missing).
Prints "changed" or "unchanged" so CI can decide whether to commit.
"""
import io
import json
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

CHANNEL_ID = "UCOClnSvKRjNZ6VH2pgvGUKg"
FEED_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
ROOT = Path(__file__).resolve().parent.parent
LATEST = ROOT / "latest.json"
THUMB = ROOT / "thumb.jpg"
UA = {"User-Agent": "Mozilla/5.0 (compatible; ceocast-links/1.0)"}
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def is_short(video_id):
    """A Short serves /shorts/<id> with 200; a normal video redirects to /watch."""
    opener = urllib.request.build_opener(NoRedirect)
    # The SOCS cookie skips the EU/UK consent redirect, which would hide the real answer.
    headers = {**UA, "Cookie": "SOCS=CAI; CONSENT=YES+1"}
    req = urllib.request.Request(f"https://www.youtube.com/shorts/{video_id}", headers=headers, method="HEAD")
    try:
        with opener.open(req, timeout=20) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        location = e.headers.get("Location", "")
        if 300 <= e.code < 400 and "/watch" in location:
            return False
        raise RuntimeError(f"Unexpected /shorts/ response for {video_id}: {e.code} {location}")


def fetch_feed(attempts=12):
    # YouTube's RSS endpoint randomly returns 404/5xx for valid channels.
    for i in range(attempts):
        try:
            return fetch(FEED_URL)
        except urllib.error.HTTPError as e:
            print(f"Feed returned {e.code}, retrying...", file=sys.stderr)
            time.sleep(min(2 + i * 2, 10))
    return None


def newest_long_video():
    feed = fetch_feed()
    if feed is None:
        # Not fatal: the next scheduled run will try again.
        print("::warning::YouTube feed unavailable after retries", file=sys.stderr)
        return None, None
    root = ET.fromstring(feed)
    for entry in root.findall("atom:entry", NS):
        vid = entry.findtext("yt:videoId", namespaces=NS)
        title = (entry.findtext("atom:title", namespaces=NS) or "").strip()
        if vid and not is_short(vid):
            return vid, title
    return None, None


def save_thumb(video_id):
    for name in ("maxresdefault", "hqdefault"):
        try:
            data = fetch(f"https://i.ytimg.com/vi/{video_id}/{name}.jpg")
        except urllib.error.HTTPError:
            continue
        img = Image.open(io.BytesIO(data)).convert("RGB")
        # Centre-crop to 16:9 (hqdefault is letterboxed 4:3), then resize.
        w, h = img.size
        th = round(w * 9 / 16)
        if th < h:
            top = (h - th) // 2
            img = img.crop((0, top, w, top + th))
        img.resize((800, 450), Image.LANCZOS).save(THUMB, "JPEG", quality=82, optimize=True, progressive=True)
        return name
    raise RuntimeError(f"No thumbnail found for {video_id}")


def main():
    current = json.loads(LATEST.read_text()) if LATEST.exists() else {}
    vid, title = newest_long_video()
    if not vid:
        print("No long-form video found in feed", file=sys.stderr)
        print("unchanged")
        return
    if vid == current.get("id") and title == current.get("title") and THUMB.exists():
        print(f"unchanged ({vid})", file=sys.stderr)
        print("unchanged")
        return
    if vid != current.get("id") or not THUMB.exists():
        source = save_thumb(vid)
        print(f"Saved thumb.jpg from {source}", file=sys.stderr)
    LATEST.write_text(json.dumps({
        "id": vid,
        "title": title,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, ensure_ascii=False, indent=2) + "\n")
    print(f"Updated to {vid}: {title}", file=sys.stderr)
    print("changed")


if __name__ == "__main__":
    main()
