"""Video discovery, descriptions and YouTube playlist HTTP endpoints."""

import json
import pathlib
import re
import subprocess
import time
import urllib.parse
import urllib.parse as up
import webbrowser

from .catalogue import add, genre_label, recent_searches, remember_search
from .database import active_profile, connect, db
from .local_media import (
    display_title,
    is_local_video,
    local_cover_url,
    local_youtube_file,
    video_metadata,
)
from .media_index import managed_downloads
from .playlists import load_playlist, playlist_entries, playlist_id, sync_playlists
from .youtube_links import YOUTUBE_STATE_TTL, youtube_link, youtube_video_state


class YoutubeRoutes:

    def local_details(self, path, requested_id):
        profile = active_profile()
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

    def downloaded_details(self, entry):
        video_id = entry["id"]
        path = entry["path"]
        profile = active_profile()
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

    def remote_details(self, video_id, preview=False):
        profile = active_profile()
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

    def playlists(self, url):
        return self.send_json(
            {
                "items": [
                    dict(row)
                    for row in db(
                        "select id,title,description,thumbnail,channel,total,loaded_at from youtube_playlists where profile=? order by title collate nocase",
                        (active_profile(),),
                    )
                ]
            }
        )

    def playlist(self, url):
        query = up.parse_qs(url.query)
        return self.send_json(
            load_playlist(
                active_profile(),
                query.get("id", [""])[0],
                refresh=query.get("refresh") == ["1"],
            )
        )

    def details(self, url):
        params = up.parse_qs(url.query)
        vid = params.get("id", [""])[0]
        if vid.startswith("local:"):
            f = pathlib.Path(vid[6:]).resolve()
            if not is_local_video(f):
                return self.send_json({"error": "Fichier local introuvable"})
            return self.send_json(self.local_details(f, vid))
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            return self.send_json({"error": "Fiche vidéo introuvable."})
        local = local_youtube_file(vid)
        if local is not None:
            return self.send_json(self.local_details(local, vid))
        downloaded = next(managed_downloads(vid), None)
        if downloaded:
            return self.send_json(self.downloaded_details(downloaded))
        return self.send_json(
            self.remote_details(vid, preview=params.get("preview") == ["1"])
        )

    def search_history(self, url):
        return self.send_json(recent_searches(active_profile()))

    def search(self, url):
        raw_query = " ".join(urllib.parse.parse_qs(url.query).get("q", [""])[0].split())
        q = raw_query[:200]
        profile = active_profile()
        if not q:
            return self.send_json(
                {"error": "Saisis un titre, un sujet ou un lien YouTube."}
            )
        video_id, link_error = youtube_link(raw_query[:2048])
        if link_error:
            return self.send_json({"error": link_error})
        if video_id:
            local = local_youtube_file(video_id)
            if local is not None:
                details = self.local_details(local, "local:" + str(local))
            else:
                download = next(managed_downloads(video_id), None)
                details = (
                    self.downloaded_details(download)
                    if download
                    else self.remote_details(video_id)
                )
            if details.get("error"):
                return self.send_json(details)
            duration = details.get("duration", 0)
            live_without_duration = not duration and details.get("youtube_state") in (
                "is_live",
                "is_upcoming",
                "post_live",
            )
            if (duration <= 180 and not live_without_duration) or re.search(
                r"#shorts|\bshorts\b", details.get("title", ""), re.I
            ):
                return self.send_json(
                    {
                        "error": "Les vidéos YouTube de 3 minutes ou moins et les Shorts sont masqués."
                    }
                )
            return self.send_json([details])
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
            return self.send_json({"error": "Recherche YouTube impossible : " + str(e)})
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
                    "channel": entry.get("channel")
                    or entry.get("uploader")
                    or "YouTube",
                    "genre": genre_label(next(iter(entry.get("categories") or []), "")),
                }
            )
        add(items)
        remember_search(profile, q, items)
        return self.send_json(items)

    def open_youtube(self, d):
        ident = d.get("id")
        if (
            self.headers.get_content_type() != "application/json"
            or not isinstance(ident, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{11}", ident)
        ):
            return self.send_json({"ok": False, "error": "Lien YouTube invalide."})
        try:
            if webbrowser.open("https://www.youtube.com/watch?v=" + ident):
                return self.send_json({"ok": True})
        except (OSError, webbrowser.Error):
            pass
        return self.send_json(
            {"ok": False, "error": "Impossible d’ouvrir YouTube dans le navigateur."}
        )

    def update_playlist(self, route, d):
        profile = active_profile()
        if route == "/api/playlists/sync":
            return self.send_json(sync_playlists(profile))
        ident = playlist_id(d.get("id") or d.get("url"))
        if not ident:
            return self.send_json({"error": "Lien de playlist invalide."})
        if route == "/api/playlist/import":
            return self.send_json(load_playlist(profile, ident, refresh=True))
        if route == "/api/playlist/remove":
            with connect() as connection:
                connection.execute(
                    "delete from youtube_playlist_items where profile=? and playlist=?",
                    (profile, ident),
                )
                connection.execute(
                    "delete from youtube_playlists where profile=? and id=?",
                    (profile, ident),
                )
            return self.send_json({"ok": True})
        if route == "/api/playlist/download":
            cached = playlist_entries(profile, ident)
            if not cached:
                return self.send_json({"error": "Playlist introuvable."})
            return self.send_json(
                self.server.downloads.prepare_trip(
                    profile,
                    d.get("ids"),
                    allowed={entry["id"]: entry["title"] for entry in cached["items"]},
                )
            )
        return self.send_error(404)
