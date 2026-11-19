"""Search YouTube and resolve direct video links."""

import json
import re
import subprocess

from fauteuil.catalogue.details import title_details
from fauteuil.catalogue.metadata import add, genre_label, remember_search
from fauteuil.youtube.links import youtube_link


def search_titles(profile, query):
    raw_query = " ".join(query.split())
    q = raw_query[:200]
    if not q:
        return {"error": "Saisis un titre, un sujet ou un lien YouTube."}
    video_id, link_error = youtube_link(raw_query[:2048])
    if link_error:
        return {"error": link_error}
    if video_id:
        details = title_details(profile, video_id, prefer_local_id=True)
        if details.get("error"):
            return details
        duration = details.get("duration", 0)
        live_without_duration = not duration and details.get("youtube_state") in (
            "is_live",
            "is_upcoming",
            "post_live",
        )
        if (duration <= 180 and not live_without_duration) or re.search(
            r"#shorts|\bshorts\b", details.get("title", ""), re.I
        ):
            return {
                "error": "Les vidéos YouTube de 3 minutes ou moins et les Shorts sont masqués."
            }
        return [details]
    try:
        raw = subprocess.check_output(
            [
                "yt-dlp",
                "--extractor-args",
                "youtube:lang=fr",
                "--flat-playlist",
                "--dump-single-json",
                f"ytsearch30:{q}",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=40,
        )
    except Exception as e:
        return {"error": "Recherche YouTube impossible : " + str(e)}
    items = []
    for entry in json.loads(raw).get("entries") or []:
        if not entry:
            continue
        video_id = entry.get("id")
        duration = entry.get("duration") or 0
        title = entry.get("title") or "Vidéo"
        if (
            not video_id
            or duration <= 180
            or re.search(r"#shorts|\bshorts\b", title, re.I)
        ):
            continue
        thumbnails = entry.get("thumbnails") or []
        thumbnail = thumbnails[-1].get("url") if thumbnails else None
        items.append(
            {
                "id": video_id,
                "title": title,
                "duration": duration,
                "thumbnail": thumbnail
                or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                "channel": entry.get("channel") or entry.get("uploader") or "YouTube",
                "genre": genre_label(next(iter(entry.get("categories") or []), "")),
            }
        )
    add(items)
    remember_search(profile, q, items)
    return items
