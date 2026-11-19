"""Playlist pagination/order, offline snapshots and profile/media boundaries."""

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fauteuil import config, database, download_batches, downloads, google
from fauteuil.gateway import GatewayServer
from fauteuil.playlists import playlist_id

PLAYLIST = "PL1234567890example"
A, B, SHORT = "aaaaaaaaaaa", "bbbbbbbbbbb", "sssssssssss"


class PlaylistTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fauteuil-playlist-test-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.data = root / "data"
        self.personal = root / "personal"
        self.personal.mkdir()
        self.enterContext(
            patch.multiple(
                config,
                DATA=self.data,
                MOVIES=self.personal,
                DB=self.data / "catalogue.sqlite3",
                DOWNLOAD_ROOT=self.data / "À regarder",
            )
        )
        self.enterContext(
            patch.dict(
                os.environ,
                CINEMA_DATA_DIR=str(self.data),
                CINEMA_MOVIES_DIR=str(self.personal),
            )
        )
        self.enterContext(
            patch.multiple(
                downloads,
                active_downloads={},
                playback_downloads={},
                download_groups={},
            )
        )
        self.enterContext(
            patch.object(
                download_batches,
                "trip",
                {"profile": None, "active": False, "items": []},
            )
        )
        database.initialize()
        self.calls = []
        self.enterContext(
            patch.object(google, "google_access_token", return_value="fixture-token")
        )
        self.enterContext(patch.object(google, "gapi", self.api))
        service = SimpleNamespace(
            download_status=downloads.download_status,
            record_progress=downloads.record_progress,
            prepare_trip=download_batches.prepare_trip,
            trip_status=download_batches.trip_status,
        )
        self.server = GatewayServer(("127.0.0.1", 0), service)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def api(self, route, params, token):
        self.calls.append((route, params.copy()))
        if route == "playlists":
            summary = {
                "id": PLAYLIST,
                "snippet": {
                    "title": "Une série",
                    "description": "Synopsis",
                    "channelTitle": "Chaîne",
                },
                "contentDetails": {"itemCount": 4},
            }
            if params.get("mine"):
                return {
                    "items": [] if params["pageToken"] else [summary],
                    **({"nextPageToken": "second"} if not params["pageToken"] else {}),
                }
            return {"items": [summary]}
        if route == "playlistItems":
            entries = (
                [(B, 4)]
                if params["pageToken"]
                else [(A, 0), (SHORT, 1), ("ddddddddddd", 2)]
            )
            return {
                "items": [
                    {
                        "snippet": {"position": position},
                        "contentDetails": {"videoId": ident},
                    }
                    for ident, position in entries
                ],
                **({"nextPageToken": "second"} if not params["pageToken"] else {}),
            }
        if route == "videos":
            return {
                "items": [
                    {
                        "id": ident,
                        "snippet": {
                            "title": "Épisode " + ident,
                            "channelTitle": "Chaîne",
                        },
                        "contentDetails": {
                            "duration": "PT1M" if ident == SHORT else "PT10M"
                        },
                    }
                    for ident in params["id"].split(",")
                    if ident != "ddddddddddd"
                ]
            }
        raise AssertionError(route)

    def request(self, path, body=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.server.server_port}" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_order_offline_next_and_download(self):
        self.assertTrue(self.request("/api/playlists/sync", {})["ok"])
        page = self.request("/api/playlist?id=" + PLAYLIST)
        self.assertEqual([entry["id"] for entry in page["items"]], [A, B])
        self.assertEqual([entry["position"] for entry in page["items"]], [0, 4])
        self.assertEqual(page["filtered"], 2)
        self.assertEqual(
            len([call for call in self.calls if call[0] == "playlistItems"]), 2
        )
        next_episode = self.request(
            "/api/next?id=" + A + "&playlist=" + PLAYLIST + "&position=0"
        )["next"]
        self.assertEqual(next_episode["id"], B)
        self.assertEqual(next_episode["playlist_position"], 4)
        self.assertIsNone(
            self.request("/api/next?id=" + B + "&playlist=" + PLAYLIST + "&position=4")[
                "next"
            ]
        )
        self.assertIsNone(
            self.request(
                "/api/next?id=wrongwrong1&playlist=" + PLAYLIST + "&position=0"
            )["next"]
        )
        self.enterContext(patch.object(google, "gapi", side_effect=OSError("offline")))
        offline = self.request("/api/playlist?id=" + PLAYLIST + "&refresh=1")
        self.assertEqual([entry["id"] for entry in offline["items"]], [A, B])
        self.assertIn("notice", offline)
        seen = []
        self.enterContext(
            patch.object(
                downloads,
                "run_download",
                lambda ident, **kwargs: seen.append((ident, kwargs["keep"]))
                or {"ok": True},
            )
        )
        result = self.request("/api/playlist/download", {"id": PLAYLIST, "ids": [A, B]})
        self.assertTrue(result["ok"])
        # Wait on the bounded worker's actual public state.
        import time

        deadline = time.monotonic() + 2
        while (
            download_batches.trip_status("default")["active"]
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        self.assertEqual(seen, [(A, True), (B, True)])
        self.assertEqual(
            database.db("select count(*) n from watchlist_profile")[0]["n"], 0
        )
        self.assertFalse(
            self.request(
                "/api/playlist/download", {"id": PLAYLIST, "ids": ["zzzzzzzzzzz"]}
            )["ok"]
        )

    def test_profile_and_remove_do_not_touch_media(self):
        self.request(
            "/api/playlist/import",
            {"url": "https://www.youtube.com/playlist?list=" + PLAYLIST},
        )
        personal = self.personal / "original.mp4"
        personal.write_bytes(b"personal source")
        managed = self.data / "À regarder/1234567890abcdef/Movies/Film/Film.mp4"
        managed.parent.mkdir(parents=True)
        managed.write_bytes(b"offline copy")
        database.db(
            "insert into temporary_downloads(id,path,created,kept) values(?,?,0,1)",
            (A, str(managed)),
            write=True,
        )
        database.db(
            "insert or replace into settings values('active_profile','different-profile')",
            write=True,
        )
        self.assertEqual(self.request("/api/playlists")["items"], [])
        self.assertIsNone(
            self.request("/api/next?id=" + A + "&playlist=" + PLAYLIST + "&position=0")[
                "next"
            ]
        )
        database.db("delete from settings where key='active_profile'", write=True)
        self.assertTrue(self.request("/api/playlist/remove", {"id": PLAYLIST})["ok"])
        self.assertEqual(self.request("/api/playlists")["items"], [])
        self.assertEqual(personal.read_bytes(), b"personal source")
        self.assertEqual(managed.read_bytes(), b"offline copy")

    def test_old_snapshot_opens_without_network_or_google(self):
        self.request(
            "/api/playlist/import",
            {"url": "https://www.youtube.com/playlist?list=" + PLAYLIST},
        )
        database.db(
            "update youtube_playlists set loaded_at=1 where id=?",
            (PLAYLIST,),
            write=True,
        )

        def unexpected_network(*args):
            raise AssertionError(
                "Opening a cached playlist must not contact Google or YouTube"
            )

        self.enterContext(
            patch.multiple(
                google, google_access_token=unexpected_network, gapi=unexpected_network
            )
        )
        cached = self.request("/api/playlist?id=" + PLAYLIST)
        self.assertEqual([entry["id"] for entry in cached["items"]], [A, B])

    def test_url_validation(self):
        parse = playlist_id
        self.assertEqual(
            parse("https://www.youtube.com/watch?v=" + A + "&list=" + PLAYLIST),
            PLAYLIST,
        )
        self.assertIsNone(parse("https://example.com/playlist?list=" + PLAYLIST))
        self.assertIsNone(parse("../private"))


if __name__ == "__main__":
    unittest.main()
