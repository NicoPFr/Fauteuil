"""Resolve video details from local files, managed downloads or YouTube."""

import json
import pathlib
import re
import subprocess
import time
import urllib.parse

from fauteuil.catalogue.metadata import add, genre_label
from fauteuil.media.index import managed_downloads
from fauteuil.media.library import (
    display_title,
    is_local_video,
    local_cover_url,
    local_youtube_file,
    video_metadata,
)
from fauteuil.storage.database import connect, db
from fauteuil.youtube.links import YOUTUBE_STATE_TTL, youtube_video_state


def local_details(profile, path, requested_id):
    local_id = "local:" + str(path)
    saved = bool(
        db(
            "select id from watchlist_profile where profile=? and id=?",
            (profile, requested_id),
        )
    )
    progress = db(
        "select current,duration,updated from playback_profile where profile=? and id=?",
        (profile, local_id),
    )
    meta = video_metadata(path)
    tags = meta["tags"]
    return {
        "id": requested_id,
        "title": display_title(path),
        "local": True,
        "description": tags.get("synopsis")
        or tags.get("description")
        or "Vidéo locale de votre bibliothèque.",
        "channel": tags.get("artist") or path.parent.name,
        "duration": int(meta["duration"]),
        "genre": genre_label(tags.get("genre")),
        "thumbnail": local_cover_url(path),
        "watchlisted": saved,
        "progress": dict(progress[0]) if progress else None,
    }


def downloaded_details(profile, entry):
    video_id = entry["id"]
    path = entry["path"]
    saved = bool(
        db(
            "select 1 from watchlist_profile where profile=? and id=?",
            (profile, video_id),
        )
    )
    progress = db(
        "select current,duration,updated from playback_profile where profile=? and id=?",
        (profile, video_id),
    )
    metadata = video_metadata(path)
    tags = metadata["tags"]
    cover = path.with_suffix(".jpg")
    return {
        "id": video_id,
        "title": display_title(path),
        "downloaded": True,
        "kept": bool(entry["kept"]),
        "description": tags.get("synopsis")
        or tags.get("description")
        or "Vidéo disponible hors ligne.",
        "channel": tags.get("artist") or "YouTube",
        "duration": int(metadata["duration"]),
        "genre": genre_label(tags.get("genre")),
        "thumbnail": (
            "/api/thumb?path=" + urllib.parse.quote(str(cover))
            if cover.is_file()
            else ""
        ),
        "watchlisted": saved,
        "progress": dict(progress[0]) if progress else None,
    }


def remote_details(profile, video_id, preview=False):
    saved = db(
        "select title,thumb,duration,channel from watchlist_profile where profile=? and id=?",
        (profile, video_id),
    )
    progress = db(
        "select current,duration,updated from playback_profile where profile=? and id=?",
        (profile, video_id),
    )
    catalogued = (
        db(
            "select title,thumb,duration,channel,genre from videos where id=?",
            (video_id,),
        )
        or saved
    )
    rows = db(
        "select description,fetched_at,youtube_state from video_synopses where id=?",
        (video_id,),
    )
    synopsis = dict(rows[0]) if rows else {}
    state = synopsis.get("youtube_state") or None
    ttl = 7 * 86400 if state == "recorded" else YOUTUBE_STATE_TTL
    fresh = bool(state and time.time() - synopsis["fetched_at"] < ttl)
    details = None
    notice = ""
    if catalogued and (fresh or preview):
        details = dict(
            catalogued[0],
            description=synopsis.get("description", ""),
            thumbnail=catalogued[0]["thumb"],
            youtube_state=state if fresh else None,
            metadata_pending=not fresh,
        )
    else:
        try:
            command = [
                "yt-dlp",
                "--no-warnings",
                "--extractor-args",
                "youtube:lang=fr",
                "--dump-single-json",
                "--skip-download",
                "--ignore-no-formats-error",
                "--socket-timeout",
                "5",
                "--extractor-retries",
                "0",
                "https://www.youtube.com/watch?v=" + video_id,
            ]
            data = json.loads(
                subprocess.check_output(
                    command, stderr=subprocess.DEVNULL, text=True, timeout=12
                )
            )
            details = {
                "id": video_id,
                "title": data.get("title") or "Vidéo YouTube",
                "description": (data.get("description") or "")[:12000],
                "channel": data.get("channel") or data.get("uploader") or "YouTube",
                "duration": data.get("duration") or 0,
                "thumbnail": data.get("thumbnail") or "",
                "genre": genre_label(next(iter(data.get("categories") or []), "")),
                "youtube_state": youtube_video_state(data),
            }
            add([details])
            with connect() as connection:
                connection.execute(
                    "insert or replace into video_synopses(id,description,fetched_at,youtube_state) values(?,?,?,?)",
                    (
                        video_id,
                        details["description"],
                        time.time(),
                        details["youtube_state"],
                    ),
                )
                connection.execute(
                    "delete from video_synopses where id in (select id from video_synopses order by fetched_at desc limit -1 offset 500)"
                )
        except (OSError, ValueError, subprocess.SubprocessError):
            if not catalogued:
                return {"error": "Impossible de récupérer la fiche sur YouTube."}
            info = catalogued[0]
            details = dict(
                info,
                description=synopsis.get("description", ""),
                thumbnail=info["thumb"],
            )
            notice = "YouTube est indisponible. Fauteuil affiche les informations enregistrées."
    details.update(
        id=video_id,
        watchlisted=bool(saved),
        progress=dict(progress[0]) if progress else None,
        notice=notice,
    )
    return details


def title_details(profile, video_id, *, preview=False, prefer_local_id=False):
    if video_id.startswith("local:"):
        path = pathlib.Path(video_id[6:]).resolve()
        if not is_local_video(path):
            return {"error": "Fichier local introuvable"}
        return local_details(profile, path, video_id)
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return {"error": "Fiche vidéo introuvable."}
    path = local_youtube_file(video_id)
    if path is not None:
        return local_details(
            profile, path, "local:" + str(path) if prefer_local_id else video_id
        )
    download = next(managed_downloads(video_id), None)
    if download:
        return downloaded_details(profile, download)
    return remote_details(profile, video_id, preview=preview)
