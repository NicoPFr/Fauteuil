"""Library, profiles, watchlist and storage HTTP endpoints."""

import secrets
import time

from fauteuil.catalogue.views import library_items, watchlist_items
from fauteuil.media.library import (
    display_title,
    imported_entries,
    register_local_videos,
)
from fauteuil.media.storage import storage_usage, unused_cover_cache
from fauteuil.storage.database import active_profile, connect, db


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
        return self.send_json(library_items(active_profile()))

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
        return self.send_json(watchlist_items(active_profile()))

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
