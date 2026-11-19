import json
import threading
from unittest.mock import patch

import test_playlists
from fauteuil import database, downloads
from fauteuil.service import download_service

A = test_playlists.A


class PlaybackCleanupTest(test_playlists.PlaylistTest):
    # Reuse the isolated HTTP harness without rerunning its playlist tests.
    test_order_offline_next_and_download = None
    test_old_snapshot_opens_without_network_or_google = None
    test_profile_and_remove_do_not_touch_media = None
    test_url_validation = None

    def fixture(self, kept=False, registered=True):
        stage = self.data / "À regarder" / "1234567890abcdef"
        media = stage / "Movies" / "Film.mp4"
        media.parent.mkdir(parents=True)
        media.write_bytes(b"fixture")
        if registered:
            self.register(media, kept)
        return stage, media

    def register(self, media, kept=False):
        database.db(
            "insert into temporary_downloads(id,path,created,kept) values(?,?,1,?)",
            (A, str(media), kept),
            write=True,
        )

    def progress(self, media, seconds, **flags):
        return self.request(
            "/api/progress",
            {"id": A, "path": str(media), "time": seconds, "duration": 120, **flags},
        )

    def test_threshold_and_personal_files(self):
        stage, media = self.fixture()
        personal = self.personal / "original.mp4"
        personal.write_bytes(b"original")
        self.assertFalse(self.progress(media, 90, closing=True)["completed"])
        self.assertTrue(stage.exists())
        self.assertTrue(self.progress(media, 91, closing=True)["completed"])
        self.assertFalse(stage.exists())
        self.assertEqual(personal.read_bytes(), b"original")

    def test_kept_download_survives_completion(self):
        _, media = self.fixture(kept=True)
        self.assertTrue(self.progress(media, 120, ended=True)["completed"])
        self.assertTrue(media.is_file())

    def test_cleanup_retries_without_losing_catalogue_entry(self):
        stage, media = self.fixture()
        with patch("shutil.rmtree", side_effect=PermissionError):
            self.assertTrue(self.progress(media, 120, ended=True)["completed"])
        self.assertTrue(media.is_file())
        self.assertIsNotNone(next(downloads.managed_downloads(A), None))
        downloads.recover_finished_downloads()
        self.assertFalse(stage.exists())
        self.assertIsNone(next(downloads.managed_downloads(A), None))

    def test_progressive_finish_before_mp4_is_registered(self):
        stage, media = self.fixture(registered=False)
        media.unlink()
        stream = stage / "stream"
        stream.mkdir()
        (stream / "index.m3u8").write_text(
            "#EXTM3U\n#EXTINF:105.0,\nsegment.m4s\n#EXT-X-ENDLIST\n"
        )
        state = stage / ".cinema-stream.json"
        state.write_text(
            json.dumps({"path": str(media), "duration": 120, "input_complete": False})
        )
        downloads.active_downloads[A] = stage
        downloads.playback_downloads[A] = {
            "stage": stage,
            "event": threading.Event(),
            "detached": False,
        }
        self.assertFalse(self.progress(media, 105, ended=True)["completed"])
        state.write_text(
            json.dumps({"path": str(media), "duration": 120, "input_complete": True})
        )
        self.assertTrue(self.progress(media, 105, ended=True)["completed"])
        self.assertTrue(stage.is_dir())
        media.write_bytes(b"finished")
        self.register(media)
        del downloads.active_downloads[A]
        downloads.retry_playback_cleanup(A)
        self.assertFalse(stage.exists())

    def test_download_service_transport_and_failure_isolation(self):
        stage, media = self.fixture()
        personal = self.personal / "original.mp4"
        personal.write_bytes(b"local fixture")
        with download_service() as client:
            self.server.downloads = client
            self.assertTrue(client.path.is_socket())
            self.assertEqual(client.path.parent.stat().st_mode & 0o777, 0o700)
            source = self.request("/api/play", {"id": A})
            self.assertEqual(source["path"], str(media))
            self.assertTrue(source["temporary"])
            self.assertEqual(
                self.request("/api/download/status?id=" + A), {"active": False}
            )
            self.assertTrue(self.progress(media, 120, ended=True)["completed"])
            self.assertFalse(stage.exists())

        self.assertFalse(client.path.parent.exists())
        self.assertIn(
            "indisponible", self.request("/api/download/status?id=" + A)["error"]
        )
        self.assertEqual(self.request("/api/profiles")["active"], "default")
        local = self.request("/api/play", {"id": "local:" + str(personal)})
        self.assertTrue(local["ok"])
        saved = self.request(
            "/api/progress", {"path": str(personal), "time": 42, "duration": 120}
        )
        self.assertTrue(saved["ok"])
        self.assertTrue(personal.is_file())
