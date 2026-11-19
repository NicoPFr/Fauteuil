"""YouTube playlists, profile snapshots and subscription suggestions."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import re
import subprocess
import threading
import time
import urllib.parse as up

from . import google
from .catalogue import add
from .database import connect, db
from .local_media import embedded_youtube_id, local_catalogue, video_metadata
from .media_index import managed_downloads
from .youtube_links import iso_duration

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


SUBSCRIPTION_CHANNEL_LIMIT = 100
SUBSCRIPTION_TTL = 20 * 60
subscription_lock = threading.Lock()
subscription_cache = None


def subscription_playlists(token):
    channels = []
    page = None
    while len(channels) < SUBSCRIPTION_CHANNEL_LIMIT:
        params = {
            "part": "snippet",
            "mine": "true",
            "maxResults": 50,
            "order": "relevance",
        }
        if page:
            params["pageToken"] = page
        response = google.gapi("subscriptions", params, token)
        for item in response.get("items", []):
            channel_id = item.get("snippet", {}).get("resourceId", {}).get("channelId")
            if channel_id and channel_id not in channels:
                channels.append(channel_id)
        next_page = response.get("nextPageToken")
        if not next_page or next_page == page:
            break
        page = next_page

    playlists = {}
    for offset in range(0, min(len(channels), SUBSCRIPTION_CHANNEL_LIMIT), 50):
        ids = channels[offset : min(offset + 50, SUBSCRIPTION_CHANNEL_LIMIT)]
        response = google.gapi(
            "channels", {"part": "contentDetails,snippet", "id": ",".join(ids)}, token
        )
        for channel in response.get("items", []):
            playlist = (
                channel.get("contentDetails", {})
                .get("relatedPlaylists", {})
                .get("uploads")
            )
            if playlist:
                playlists[playlist] = channel.get("snippet", {}).get(
                    "title", "Abonnement"
                )
    return playlists


def subscription_uploads(playlist, channel, token):
    params = {
        "part": "snippet,contentDetails",
        "playlistId": playlist,
        "maxResults": 10,
    }
    items = []
    for video in google.gapi("playlistItems", params, token).get("items", []):
        snippet = video.get("snippet", {})
        content = video.get("contentDetails", {})
        video_id = content.get("videoId") or snippet.get("resourceId", {}).get(
            "videoId"
        )
        title = snippet.get("title", "")
        if (
            not video_id
            or not title
            or title in ("Private video", "Deleted video")
            or re.search(r"#shorts|\bshorts\b", title, re.I)
        ):
            continue
        thumbnails = snippet.get("thumbnails", {})
        cover = thumbnails.get("high", thumbnails.get("medium", {}))
        items.append(
            {
                "id": video_id,
                "title": title,
                "thumbnail": cover.get("url", ""),
                "channel": channel,
                "published_at": content.get("videoPublishedAt")
                or snippet.get("publishedAt", ""),
            }
        )
    return items


def subscription_suggestions(token):
    global subscription_cache
    fingerprint = hashlib.sha256(token.encode()).digest()
    with subscription_lock:
        if (
            subscription_cache
            and subscription_cache[0] == fingerprint
            and subscription_cache[1] > time.monotonic()
        ):
            return subscription_cache[2]
        playlists = subscription_playlists(token)
        uploads = []
        failures = []
        with ThreadPoolExecutor(max_workers=10) as workers:
            futures = [
                workers.submit(subscription_uploads, playlist, channel, token)
                for playlist, channel in playlists.items()
            ]
            for future in as_completed(futures):
                try:
                    uploads.extend(future.result())
                except (OSError, ValueError) as error:
                    failures.append(error)
        if playlists and len(failures) == len(playlists):
            raise failures[0]
        uploads.sort(
            key=lambda video: (video["published_at"], video["id"]), reverse=True
        )
        unique = {}
        for video in uploads:
            unique.setdefault(video["id"], video)
        ids = list(unique)
        suggestions = []
        for offset in range(0, len(ids), 50):
            batch = ids[offset : offset + 50]
            response = google.gapi(
                "videos", {"part": "contentDetails", "id": ",".join(batch)}, token
            )
            durations = {
                row["id"]: iso_duration(row.get("contentDetails", {}).get("duration"))
                for row in response.get("items", [])
            }
            suggestions.extend(
                dict(unique[video_id], duration=durations[video_id])
                for video_id in batch
                if durations.get(video_id, 0) > 180
            )
            if len(suggestions) >= 48:
                break
        result = {"connected": True, "items": suggestions[:48]}
        if failures:
            result["notice"] = "Certaines chaînes n’ont pas pu être actualisées."
        if suggestions:
            add(suggestions[:48])
        subscription_cache = (
            fingerprint,
            time.monotonic() + (120 if failures else SUBSCRIPTION_TTL),
            result,
        )
        return result
