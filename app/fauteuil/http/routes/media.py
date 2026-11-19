"""Byte-range media, HLS, subtitles, covers and playback HTTP endpoints."""

import os
import pathlib
import re
import urllib.parse
import urllib.parse as up

from fauteuil import config
from fauteuil.downloads.client import DownloadServiceUnavailable
from fauteuil.media.index import playback_source
from fauteuil.media.library import (
    VIDEO_CONTENT_TYPES,
    display_title,
    is_local_video,
    local_catalogue,
    local_cover,
    local_youtube_file,
)
from fauteuil.media.progress import record_progress
from fauteuil.storage.database import active_profile, db
from fauteuil.storage.files import regular_file_size
from fauteuil.youtube.playlists import playlist_entries, playlist_id


class MediaRoutes:
    def send_media(self, query):
        path = pathlib.Path(urllib.parse.parse_qs(query).get("path", [""])[0]).resolve()
        temporary_root = config.DOWNLOAD_ROOT.resolve()
        content_type = VIDEO_CONTENT_TYPES.get(path.suffix.lower())
        if (
            not content_type
            or not path.is_file()
            or (temporary_root not in path.parents and not is_local_video(path))
        ):
            self.send_error(404)
            return
        try:
            stream = path.open("rb")
        except OSError:
            self.send_error(404)
            return
        with stream:
            size = os.fstat(stream.fileno()).st_size
            requested_range = (
                self.headers.get("Range") if self.command == "GET" else None
            )
            start, end = 0, size - 1
            if requested_range:
                match = re.fullmatch(r"bytes=(\d{0,20})-(\d{0,20})", requested_range)
                if match and match[1]:
                    start = int(match[1])
                    end = min(end, int(match[2])) if match[2] else end
                elif match and match[2]:
                    start = max(0, size - int(match[2]))
                else:
                    start = size
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            self.send_response(206 if requested_range else 200)
            if requested_range:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            if self.command == "HEAD":
                return
            stream.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                remaining -= len(chunk)

    def send_stream(self, route):
        match = re.fullmatch(
            r"/api/stream/([0-9a-f]{16})/(index\.m3u8|init\.mp4|segment[0-9]{6}\.m4s)",
            route,
        )
        if not match:
            return self.send_error(404)
        stage = (config.DOWNLOAD_ROOT / match[1]).resolve()
        if stage.parent != config.DOWNLOAD_ROOT.resolve():
            return self.send_error(404)
        registered = any(
            pathlib.Path(row["path"]).resolve().is_relative_to(stage)
            for row in db("select path from temporary_downloads")
        )
        if not registered:
            try:
                if not self.server.downloads.is_active_stage(match[1]):
                    return self.send_error(404)
            except DownloadServiceUnavailable:
                return self.send_error(503)
        folder = stage / "stream"
        path = folder / match[2]
        if folder.is_symlink() or path.is_symlink() or not path.is_file():
            return self.send_error(404)
        body = path.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/vnd.apple.mpegurl" if path.suffix == ".m3u8" else "video/mp4",
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_HEAD(self):
        url = urllib.parse.urlparse(self.path)
        if url.path.startswith("/api/stream/"):
            return self.send_stream(url.path)
        if url.path == "/api/media":
            return self.send_media(url.query)
        return super().do_HEAD()

    def download_trip_status(self, url):
        return self.send_json(self.server.downloads.trip_status(active_profile()))

    def download_progress(self, url):
        video_id = urllib.parse.parse_qs(url.query).get("id", [""])[0]
        return self.send_json(self.server.downloads.download_status(video_id))

    def subtitle(self, url):
        f = pathlib.Path(
            urllib.parse.parse_qs(url.query).get("path", [""])[0]
        ).resolve()
        allowed = [config.MOVIES.resolve(), config.DOWNLOAD_ROOT.resolve()]
        if not f.is_file() or not (
            any(root in f.parents for root in allowed) or is_local_video(f)
        ):
            self.send_error(404)
            return
        candidates = [f.with_name(f.stem + ".fr.vtt"), f.with_suffix(".vtt")]
        sub = next((p for p in candidates if p.is_file() and not p.is_symlink()), None)
        if not sub:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/vtt; charset=utf-8")
        self.end_headers()
        self.wfile.write(sub.read_bytes())
        return

    def next_title(self, url):
        vid = urllib.parse.parse_qs(url.query).get("id", [""])[0]
        query = up.parse_qs(url.query)
        playlist = playlist_id(query.get("playlist", [""])[0])
        if playlist:
            cached = playlist_entries(active_profile(), playlist)
            try:
                position = int(query.get("position", ["-1"])[0])
            except ValueError:
                return self.send_json({"next": None})
            entries = cached["items"] if cached else []
            current = next(
                (entry for entry in entries if entry["position"] == position), None
            )
            if not current:
                return self.send_json({"next": None})
            local = (
                local_youtube_file(current["id"]) if vid.startswith("local:") else None
            )
            if vid != current["id"] and (local is None or vid != "local:" + str(local)):
                return self.send_json({"next": None})
            following = next(
                (entry for entry in entries if entry["position"] > position), None
            )
            if not following:
                return self.send_json({"next": None})
            return self.send_json(
                {
                    "next": dict(
                        following,
                        playlist_id=playlist,
                        playlist_position=following["position"],
                        start=(following.get("progress") or {}).get("current", 0),
                    )
                }
            )
        if not vid.startswith("local:"):
            return self.send_json({"next": None})
        f = pathlib.Path(vid[6:]).resolve()
        if not is_local_video(f):
            return self.send_json({"next": None})
        catalogue = local_catalogue()
        series_root = next((root for path, root, _ in catalogue if path == f), None)
        if series_root is None:
            return self.send_json({"next": None})
        files = [path for path, root, _ in catalogue if root == series_root]
        try:
            i = files.index(f)
        except ValueError:
            return self.send_json({"next": None})
        if i + 1 >= len(files):
            return self.send_json({"next": None})
        nxt = files[i + 1].resolve()
        nid = "local:" + str(nxt)
        pr = db(
            "select current,duration from playback_profile where profile=? and id=?",
            (active_profile(), nid),
        )
        start = (
            float(pr[0]["current"])
            if pr and pr[0]["duration"] - pr[0]["current"] > 30
            else 0
        )
        return self.send_json(
            {
                "next": {
                    "id": nid,
                    "path": str(nxt),
                    "title": display_title(nxt),
                    "thumbnail": "/api/thumb?video=" + urllib.parse.quote(str(nxt)),
                    "start": start,
                    "media_url": "/api/media?path=" + urllib.parse.quote(str(nxt)),
                    "subtitle_url": "/api/subtitle?path="
                    + urllib.parse.quote(str(nxt)),
                }
            }
        )

    def thumbnail(self, url):
        query = up.parse_qs(url.query)
        if "video" in query:
            source = pathlib.Path(query["video"][0]).resolve()
            f = local_cover(source) if is_local_video(source) else None
        else:
            f = pathlib.Path(query.get("path", [""])[0]).resolve()
            downloaded_cover = f.suffix == ".jpg" and bool(
                db(
                    "select 1 from temporary_downloads where path=?",
                    (str(f.with_suffix(".mp4")),),
                )
            )
            if not (
                config.MOVIES.resolve() in f.parents
                or (config.DATA / "jaquettes").resolve() in f.parents
                or downloaded_cover
            ):
                f = None
        content_type = (
            {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
            }.get(f.suffix.lower())
            if f
            else None
        )
        if not content_type or not regular_file_size(f):
            self.send_error(404)
            return
        try:
            cover = f.read_bytes()
        except OSError:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(cover)
        return

    def start_trip(self, d):
        return self.send_json(
            self.server.downloads.prepare_trip(active_profile(), d.get("ids"))
        )

    def cancel_download_trip(self, d):
        if self.headers.get_content_type() != "application/json":
            self.send_error(415)
            return
        return self.send_json(self.server.downloads.cancel_trip(active_profile()))

    def remove_download(self, d):
        if self.headers.get_content_type() != "application/json":
            self.send_error(415)
            return
        ident = d.get("id")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", ident):
            return self.send_json({"ok": False, "error": "Téléchargement introuvable."})
        return self.send_json(self.server.downloads.remove_managed_download(ident))

    def update_progress(self, d):
        try:
            path = pathlib.Path(d.get("path", "")).resolve()
        except (OSError, TypeError, ValueError):
            return self.send_json({"ok": False, "error": "Vidéo introuvable."})
        profile = active_profile()
        if config.DOWNLOAD_ROOT.resolve() in path.parents:
            return self.send_json(self.server.downloads.record_progress(d, profile))
        return self.send_json(record_progress(d, profile))

    def start_playback_or_download(self, route, d):
        video_id = d.get("id")
        if isinstance(video_id, str) and video_id.startswith("local:"):
            path = pathlib.Path(video_id[6:]).resolve()
            if not is_local_video(path):
                return self.send_json(
                    {"ok": False, "error": "Le fichier vidéo local est introuvable."}
                )
            return self.send_json(playback_source(path))
        if not isinstance(video_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{11}", video_id
        ):
            return self.send_json(
                {"ok": False, "error": "Identifiant YouTube invalide."}
            )

        if route == "/api/play":
            return self.send_json(
                self.server.downloads.progressive_source(
                    video_id,
                    start=d.get("start", 0),
                    wait_for_file=d.get("wait_for_file") is True,
                )
            )
        return self.send_json(self.server.downloads.run_download(video_id, keep=True))
