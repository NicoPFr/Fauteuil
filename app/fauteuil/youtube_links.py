"""YouTube URL parsing and video classification."""

import re
import urllib.parse


def youtube_link(value):
    try:
        url = urllib.parse.urlparse(value.strip())
        if url.scheme not in ("http", "https"):
            return None, None
        host = url.hostname
    except ValueError:
        return None, None
    if host not in ("www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"):
        return None, None
    path = url.path.rstrip("/")
    if path.startswith("/shorts/"):
        return None, "Les YouTube Shorts sont masqués dans Fauteuil."
    if host == "youtu.be":
        video_id = path.lstrip("/")
    elif path == "/watch":
        video_id = urllib.parse.parse_qs(url.query).get("v", [""])[0]
    else:
        video_id = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return None, "Colle un lien vers une vidéo YouTube individuelle."
    return video_id, None


def iso_duration(value):
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    return (
        int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)
        if m
        else 0
    )


YOUTUBE_STATE_TTL = 5 * 60


def youtube_video_state(data):
    live_status = data.get("live_status")
    if live_status in ("is_live", "is_upcoming", "post_live"):
        return live_status
    if (
        data.get("availability")
        in ("private", "premium_only", "subscriber_only", "needs_auth")
        or data.get("formats") == []
    ):
        return "unavailable"
    return "recorded"
