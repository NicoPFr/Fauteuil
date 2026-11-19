"""Video discovery, descriptions and YouTube playlist HTTP endpoints."""

import re
import webbrowser
from urllib.parse import parse_qs

from fauteuil.catalogue.details import title_details
from fauteuil.catalogue.metadata import recent_searches
from fauteuil.storage.database import active_profile, connect, db
from fauteuil.youtube.playlists import (
    load_playlist,
    playlist_entries,
    playlist_id,
    sync_playlists,
)
from fauteuil.youtube.search import search_titles


class YoutubeRoutes:
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
        query = parse_qs(url.query)
        return self.send_json(
            load_playlist(
                active_profile(),
                query.get("id", [""])[0],
                refresh=query.get("refresh") == ["1"],
            )
        )

    def details(self, url):
        params = parse_qs(url.query)
        return self.send_json(
            title_details(
                active_profile(),
                params.get("id", [""])[0],
                preview=params.get("preview") == ["1"],
            )
        )

    def search_history(self, url):
        return self.send_json(recent_searches(active_profile()))

    def search(self, url):
        query = parse_qs(url.query).get("q", [""])[0]
        return self.send_json(search_titles(active_profile(), query))

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
