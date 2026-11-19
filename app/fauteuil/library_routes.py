"""Library, profiles, watchlist and storage HTTP endpoints."""

import re
import secrets
import time
import urllib.parse

from .catalogue import genre_label, storage_usage, unused_cover_cache
from .database import active_profile, connect, db
from .local_media import (
    display_title,
    embedded_youtube_id,
    imported_entries,
    local_catalogue,
    local_cover_url,
    register_local_videos,
    series_identifier,
    video_metadata,
)
from .media_index import managed_downloads


class LibraryRoutes:
    def local_imports(self, url):
        entries = [
            {"path": str(path), "title": display_title(path), "available": available}
            for path, available in imported_entries()
        ]
        return self.send_json(entries)

    def storage(self, url):
        try:
            return self.send_json(storage_usage())
        except OSError:
            return self.send_json(
                {"error": "Impossible de mesurer l’espace de stockage."}
            )

    def library(self, url):
        profile = active_profile()
        downloads = {entry["id"]: entry for entry in managed_downloads()}
        saved = {
            r["id"]
            for r in db("select id from watchlist_profile where profile=?", (profile,))
        }
        progress = {
            r["id"]: dict(r)
            for r in db(
                "select id,current,duration,updated from playback_profile where profile=?",
                (profile,),
            )
        }
        finished = {
            row["id"]: row["finished_at"]
            for row in db(
                "select id,finished_at from completed_playback where profile=?",
                (profile,),
            )
        }
        items = [
            dict(x)
            for x in db("select * from videos order by title")
            if int(x["duration"] or 0) > 180
            and not re.search(r"#shorts|\bshorts\b", x["title"] or "", re.I)
        ]
        for x in items:
            x["thumbnail"] = x.get("thumb", "")
            x["watchlisted"] = x["id"] in saved
            x["progress"] = progress.get(x["id"])
            x["finished_at"] = finished.get(x["id"])
        known = {item["id"] for item in items}
        for video_id, entry in downloads.items():
            if video_id in known:
                continue
            metadata = video_metadata(entry["path"])
            items.append(
                {
                    "id": video_id,
                    "title": display_title(entry["path"]),
                    "duration": int(metadata["duration"]),
                    "channel": metadata["tags"].get("artist") or "YouTube",
                    "genre": genre_label(metadata["tags"].get("genre")),
                    "watchlisted": video_id in saved,
                    "progress": progress.get(video_id),
                    "finished_at": finished.get(video_id),
                }
            )
        for item in items:
            entry = downloads.get(item["id"])
            if entry:
                item["downloaded"] = True
                item["kept"] = bool(entry["kept"])
                if entry["kept"]:
                    item["added_at"] = entry["created"]
                cover = entry["path"].with_suffix(".jpg")
                if cover.is_file():
                    item["thumbnail"] = "/api/thumb?path=" + urllib.parse.quote(
                        str(cover)
                    )
        imported = {
            row["path"]: row["added_at"]
            for row in db("select path,added_at from imported_videos")
        }
        for path, series_root, season in local_catalogue():
            try:
                file_stat = path.stat()
            except OSError:
                continue
            added_at = imported.get(
                str(path), getattr(file_stat, "st_birthtime", file_stat.st_mtime)
            )
            metadata = video_metadata(path)
            video_id = "local:" + str(path)
            items.append(
                {
                    "id": video_id,
                    "title": display_title(path),
                    "thumbnail": local_cover_url(path),
                    "duration": int(metadata["duration"]),
                    "channel": metadata["tags"].get("artist") or "Sur cet appareil",
                    "genre": genre_label(metadata["tags"].get("genre")),
                    "local": True,
                    "path": str(path),
                    "source_id": embedded_youtube_id(metadata["tags"]),
                    "added_at": added_at,
                    "collection": (
                        series_root.name.replace("_", " ") if series_root else "Films"
                    ),
                    "series_id": (
                        series_identifier(series_root) if series_root else None
                    ),
                    "season": "Saison " + str(season) if season else "",
                    "season_number": season,
                    "watchlisted": video_id in saved,
                    "progress": progress.get(video_id),
                    "finished_at": finished.get(video_id),
                }
            )
        return self.send_json(items)

    def profiles(self, url):
        return self.send_json(
            {
                "active": active_profile(),
                "profiles": [
                    dict(x)
                    for x in db("select id,name,avatar from profiles order by rowid")
                ],
            }
        )

    def watchlist(self, url):
        rows = [
            dict(x)
            for x in db(
                "select id,title,thumb,duration,channel,added from watchlist_profile where profile=? order by added desc",
                (active_profile(),),
            )
            if int(x["duration"] or 0) > 180
            and not re.search(r"#shorts|\bshorts\b", x["title"] or "", re.I)
        ]
        for x in rows:
            x["thumbnail"] = x.pop("thumb", "")
            x["watchlisted"] = True
        return self.send_json(rows)

    def clean_covers(self, d):
        if self.headers.get_content_type() != "application/json":
            self.send_error(415)
            return
        try:
            return self.send_json(dict(unused_cover_cache(remove=True), ok=True))
        except OSError:
            return self.send_json(
                {
                    "ok": False,
                    "error": "Fauteuil n’a pas pu nettoyer toutes les jaquettes.",
                }
            )

    def unlink(self, d):
        if self.headers.get_content_type() != "application/json":
            self.send_error(415)
            return
        path = d.get("path")
        if not isinstance(path, str):
            return self.send_json({"ok": False, "error": "Référence invalide."})
        with connect() as connection:
            removed = connection.execute(
                "delete from imported_videos where path=?", (path,)
            ).rowcount
        return self.send_json(
            {"ok": bool(removed)}
            if removed
            else {"ok": False, "error": "Référence introuvable."}
        )

    def import_videos(self, d):
        if not self.native_request():
            self.send_error(403)
            return
        paths = d.get("paths")
        if not isinstance(paths, list) or not 1 <= len(paths) <= 1000:
            return self.send_json(
                {"ok": False, "error": "Sélectionne entre une et mille vidéos."}
            )
        return self.send_json(register_local_videos(paths))

    def create_profile(self, d):
        name = d.get("name", "").strip()[:40]
        if not name:
            return self.send_json({"error": "Donne un nom au profil."})
        pid = secrets.token_hex(8)
        avatar = d.get("avatar", "🍿")
        with connect() as c:
            c.execute("insert into profiles values(?,?,?)", (pid, name, avatar))
            c.execute(
                "insert or replace into settings values(?,?)", ("active_profile", pid)
            )
        return self.send_json({"ok": True, "id": pid})

    def select_profile(self, d):
        pid = d.get("id", "")
        exists = db("select id from profiles where id=?", (pid,))
        if not exists:
            return self.send_json({"error": "Profil introuvable."})
        with connect() as c:
            c.execute(
                "insert or replace into settings values(?,?)", ("active_profile", pid)
            )
        return self.send_json({"ok": True})

    def delete_profile(self, d):
        pid = d.get("id", "")
        if pid == "default":
            return self.send_json(
                {"error": "Le profil Principal ne peut pas être supprimé."}
            )
        with connect() as c:
            c.execute("delete from profiles where id=?", (pid,))
            c.execute("delete from watchlist_profile where profile=?", (pid,))
            c.execute("delete from playback_profile where profile=?", (pid,))
            c.execute("delete from recent_searches where profile=?", (pid,))
            c.execute("delete from completed_playback where profile=?", (pid,))
            c.execute("delete from youtube_playlists where profile=?", (pid,))
            c.execute("delete from youtube_playlist_items where profile=?", (pid,))
        if active_profile() == pid:
            with connect() as c:
                c.execute(
                    "insert or replace into settings values(?,?)",
                    ("active_profile", "default"),
                )
        return self.send_json({"ok": True})

    def update_watchlist(self, d):
        vid = d.get("id", "")
        if d.get("remove"):
            with connect() as c:
                c.execute(
                    "delete from watchlist_profile where profile=? and id=?",
                    (active_profile(), vid),
                )
            return self.send_json({"ok": True, "saved": False})
        row = db("select * from videos where id=?", (vid,))
        if row:
            x = dict(row[0])
            thumb = x["thumb"]
            title = x["title"]
            duration = x["duration"]
            channel = x["channel"]
        else:
            x = {
                "title": d.get("title", "Vidéo"),
                "thumbnail": d.get("thumbnail", ""),
                "duration": d.get("duration", 0),
                "channel": d.get("channel", "YouTube"),
            }
            thumb = x["thumbnail"]
            title = x["title"]
            duration = x["duration"]
            channel = x["channel"]
        with connect() as c:
            c.execute(
                "insert or replace into watchlist_profile values(?,?,?,?,?,?,?)",
                (active_profile(), vid, title, thumb, duration, channel, time.time()),
            )
        return self.send_json({"ok": True, "saved": True})
