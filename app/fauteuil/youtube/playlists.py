"""YouTube playlists and their offline snapshots."""

import json
import re
import subprocess
import threading
import time
import urllib.parse as up

from fauteuil.catalogue.metadata import add
from fauteuil.media.index import managed_downloads
from fauteuil.media.library import embedded_youtube_id, local_catalogue, video_metadata
from fauteuil.storage.database import connect, db
from fauteuil.youtube import google
from fauteuil.youtube.links import iso_duration

playlist_lock = threading.Lock()


def playlist_id(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = up.urlparse(value.strip())
        if parsed.scheme:
            if parsed.scheme not in ("http", "https") or parsed.hostname not in (
                "youtube.com",
                "www.youtube.com",
                "m.youtube.com",
                "youtu.be",
            ):
                return None
            value = up.parse_qs(parsed.query).get("list", [""])[0]
    except ValueError:
        return None
    return value if re.fullmatch(r"[A-Za-z0-9_-]{10,120}", value) else None


def playlist_summary(row):
    sn = row.get("snippet", {})
    thumbs = sn.get("thumbnails", {})
    return {
        "id": row["id"],
        "title": sn.get("title", "Playlist"),
        "description": sn.get("description", ""),
        "thumbnail": thumbs.get(
            "high", thumbs.get("medium", thumbs.get("default", {}))
        ).get("url", ""),
        "channel": sn.get("channelTitle", "YouTube"),
        "total": row.get("contentDetails", {}).get("itemCount", 0),
    }


def save_playlist(profile, item):
    db(
        """insert into youtube_playlists(profile,id,title,description,thumbnail,channel,total,loaded_at,synced_at)
       values(?,?,?,?,?,?,?,0,?) on conflict(profile,id) do update set title=excluded.title,
       description=excluded.description,thumbnail=excluded.thumbnail,channel=excluded.channel,total=excluded.total,synced_at=excluded.synced_at""",
        (
            profile,
            item["id"],
            item["title"],
            item.get("description", ""),
            item.get("thumbnail", ""),
            item.get("channel", "YouTube"),
            item.get("total", 0),
            time.time(),
        ),
        write=True,
    )


def sync_playlists(profile):
    try:
        token = google.google_access_token()
        if not token:
            return {
                "ok": False,
                "error": "Connecte ton compte Google pour retrouver tes playlists.",
            }
        page = ""
        count = 0
        while True:
            response = google.gapi(
                "playlists",
                {
                    "part": "snippet,contentDetails",
                    "mine": "true",
                    "maxResults": 50,
                    "pageToken": page,
                },
                token,
            )
            for row in response.get("items", []):
                save_playlist(profile, playlist_summary(row))
                count += 1
            page = response.get("nextPageToken", "")
            if not page:
                break
        return {"ok": True, "count": count}
    except (OSError, ValueError):
        return {
            "ok": False,
            "error": "YouTube n’a pas pu actualiser tes playlists. Les collections déjà enregistrées restent disponibles.",
        }


def playlist_entries(profile, ident):
    rows = db(
        "select * from youtube_playlists where profile=? and id=?", (profile, ident)
    )
    if not rows:
        return None
    result = dict(rows[0])
    result.pop("profile", None)
    entries = [
        dict(row)
        for row in db(
            """select v.*,p.position from youtube_playlist_items p join videos v on v.id=p.video
         where p.profile=? and p.playlist=? order by p.position""",
            (profile, ident),
        )
    ]
    downloads = {row["id"]: row for row in managed_downloads()}
    progress = {
        row["id"]: dict(row)
        for row in db(
            "select id,current,duration,updated from playback_profile where profile=?",
            (profile,),
        )
    }
    completed = {
        row["id"]
        for row in db("select id from completed_playback where profile=?", (profile,))
    }
    local_files = {
        embedded_youtube_id(video_metadata(path)["tags"]): path
        for path, _, _ in local_catalogue()
    }
    for entry in entries:
        entry["thumbnail"] = entry.pop("thumb", "")
        local = local_files.get(entry["id"])
        local_id = "local:" + str(local) if local else None
        if local:
            entry["thumbnail"] = "/api/thumb?video=" + up.quote(str(local))
        elif entry["id"] in downloads:
            cover = downloads[entry["id"]]["path"].with_suffix(".jpg")
            if cover.is_file():
                entry["thumbnail"] = "/api/thumb?path=" + up.quote(str(cover))
        entry.update(
            available=bool(local or entry["id"] in downloads),
            kept=bool(local or downloads.get(entry["id"], {}).get("kept")),
            progress=progress.get(local_id) or progress.get(entry["id"]),
            finished=entry["id"] in completed or local_id in completed,
        )
    result["items"] = entries
    result["filtered"] = (
        max(0, result["total"] - len(entries)) if result["loaded_at"] else 0
    )
    return result


def load_playlist(profile, ident, refresh=False):
    if not playlist_id(ident):
        return {"error": "Lien de playlist YouTube invalide."}
    with playlist_lock:
        cached = playlist_entries(profile, ident)
        # Opening a saved collection must never wait for Internet access. Refresh is explicit.
        if cached and cached["loaded_at"] and not refresh:
            return cached
        try:
            token = google.google_access_token()
            entries = []
            if token:
                response = google.gapi(
                    "playlists", {"part": "snippet,contentDetails", "id": ident}, token
                )
                if not response.get("items"):
                    raise ValueError("Playlist inaccessible")
                summary = playlist_summary(response["items"][0])
                page = ""
                while True:
                    response = google.gapi(
                        "playlistItems",
                        {
                            "part": "snippet,contentDetails",
                            "playlistId": ident,
                            "maxResults": 50,
                            "pageToken": page,
                        },
                        token,
                    )
                    items = response.get("items", [])
                    ids = list(
                        dict.fromkeys(
                            row.get("contentDetails", {}).get("videoId", "")
                            for row in items
                        )
                    )
                    ids = [i for i in ids if re.fullmatch(r"[A-Za-z0-9_-]{11}", i)]
                    details = (
                        google.gapi(
                            "videos",
                            {
                                "part": "snippet,contentDetails,status",
                                "id": ",".join(ids),
                            },
                            token,
                        )
                        if ids
                        else {"items": []}
                    )
                    videos = {row["id"]: row for row in details.get("items", [])}
                    for row in items:
                        vid = row.get("contentDetails", {}).get("videoId")
                        video = videos.get(vid)
                        if not video:
                            continue
                        sn = video["snippet"]
                        duration = iso_duration(
                            video.get("contentDetails", {}).get("duration", "")
                        )
                        title = sn.get("title", "Vidéo")
                        if duration <= 180 or re.search(
                            r"#shorts|\bshorts\b", title, re.I
                        ):
                            continue
                        thumbs = sn.get("thumbnails", {})
                        entries.append(
                            {
                                "id": vid,
                                "title": title,
                                "duration": duration,
                                "channel": sn.get("channelTitle", "YouTube"),
                                "thumbnail": thumbs.get(
                                    "high", thumbs.get("medium", {})
                                ).get(
                                    "url", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                                ),
                                "position": row.get("snippet", {}).get(
                                    "position", len(entries)
                                ),
                            }
                        )
                    page = response.get("nextPageToken", "")
                    if not page:
                        break
            else:
                raw = subprocess.check_output(
                    [
                        "yt-dlp",
                        "--no-warnings",
                        "--flat-playlist",
                        "--extractor-args",
                        "youtube:lang=fr",
                        "--dump-single-json",
                        "--",
                        "https://www.youtube.com/playlist?list=" + ident,
                    ],
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=90,
                )
                info = json.loads(raw)
                raw_entries = info.get("entries") or []
                summary = {
                    "id": ident,
                    "title": info.get("title", "Playlist"),
                    "description": info.get("description", ""),
                    "channel": info.get("channel") or info.get("uploader", "YouTube"),
                    "total": len(raw_entries),
                    "thumbnail": "",
                }
                for position, entry in enumerate(raw_entries):
                    if not entry:
                        continue
                    vid = entry.get("id", "")
                    title = entry.get("title", "Vidéo")
                    duration = entry.get("duration") or 0
                    if (
                        not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid)
                        or duration <= 180
                        or re.search(r"#shorts|\bshorts\b", title, re.I)
                    ):
                        continue
                    entries.append(
                        {
                            "id": vid,
                            "title": title,
                            "duration": duration,
                            "channel": entry.get("channel")
                            or entry.get("uploader", "YouTube"),
                            "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                            "position": position,
                        }
                    )
                summary["thumbnail"] = entries[0]["thumbnail"] if entries else ""
            # Commit the complete ordered snapshot together. Offline use never sees half a refresh.
            save_playlist(profile, summary)
            add(entries)
            with connect() as connection:
                connection.execute(
                    "delete from youtube_playlist_items where profile=? and playlist=?",
                    (profile, ident),
                )
                connection.executemany(
                    "insert into youtube_playlist_items values(?,?,?,?)",
                    [(profile, ident, e["position"], e["id"]) for e in entries],
                )
                connection.execute(
                    "update youtube_playlists set loaded_at=? where profile=? and id=?",
                    (time.time(), profile, ident),
                )
            return playlist_entries(profile, ident)
        except (OSError, ValueError, subprocess.SubprocessError):
            if cached and cached["loaded_at"]:
                cached["notice"] = (
                    "YouTube est indisponible. Voici la playlist enregistrée sur le Mac."
                )
                return cached
            return {
                "error": "Playlist inaccessible. Vérifie son lien et ta connexion Google si elle est privée."
            }
