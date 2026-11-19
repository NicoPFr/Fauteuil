"""The loopback HTTP gateway used by the native window and OAuth callback."""

import json
import os
import secrets
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import config
from .download_client import DownloadClient, DownloadServiceUnavailable
from .google_routes import GoogleRoutes
from .library_routes import LibraryRoutes
from .media_routes import MediaRoutes
from .youtube_routes import YoutubeRoutes


class GatewayServer(ThreadingHTTPServer):
    def __init__(self, address, downloads=None):
        self.downloads = downloads if downloads is not None else DownloadClient(None)
        super().__init__(address, GatewayHandler)


class GatewayHandler(
    MediaRoutes, LibraryRoutes, YoutubeRoutes, GoogleRoutes, SimpleHTTPRequestHandler
):
    GET_ROUTES = {
        "/api/local/imports": LibraryRoutes.local_imports,
        "/api/download/trip": MediaRoutes.download_trip_status,
        "/api/download/status": MediaRoutes.download_progress,
        "/api/playlists": YoutubeRoutes.playlists,
        "/api/playlist": YoutubeRoutes.playlist,
        "/api/storage": LibraryRoutes.storage,
        "/api/library": LibraryRoutes.library,
        "/api/profiles": LibraryRoutes.profiles,
        "/api/watchlist": LibraryRoutes.watchlist,
        "/api/google/trending": GoogleRoutes.google_trending,
        "/api/google/suggestions": GoogleRoutes.google_suggestions,
        "/api/google/status": GoogleRoutes.google_status,
        "/api/subtitle": MediaRoutes.subtitle,
        "/api/next": MediaRoutes.next_title,
        "/api/thumb": MediaRoutes.thumbnail,
        "/api/details": YoutubeRoutes.details,
        "/api/search-history": YoutubeRoutes.search_history,
        "/api/search": YoutubeRoutes.search,
    }
    POST_ROUTES = {
        "/api/storage/clean-covers": LibraryRoutes.clean_covers,
        "/api/local/unlink": LibraryRoutes.unlink,
        "/api/local/import": LibraryRoutes.import_videos,
        "/api/profiles": LibraryRoutes.create_profile,
        "/api/profiles/select": LibraryRoutes.select_profile,
        "/api/profiles/delete": LibraryRoutes.delete_profile,
        "/api/google/setup": GoogleRoutes.google_setup,
        "/api/google/settings": GoogleRoutes.google_settings,
        "/api/google/disconnect": GoogleRoutes.google_disconnect,
        "/api/google/start": GoogleRoutes.google_start,
        "/api/progress": MediaRoutes.update_progress,
        "/api/watchlist": LibraryRoutes.update_watchlist,
        "/api/settings": GoogleRoutes.legacy_settings,
        "/api/download/trip": MediaRoutes.start_trip,
        "/api/download/trip/cancel": MediaRoutes.cancel_download_trip,
        "/api/download/remove": MediaRoutes.remove_download,
        "/api/youtube/open": YoutubeRoutes.open_youtube,
    }

    def parse_request(self):
        if not super().parse_request():
            return False
        port = self.server.server_port
        hosts = (f"127.0.0.1:{port}", f"localhost:{port}")
        if self.headers.get("Host") not in hosts:
            self.send_error(403, "Serveur local")
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in tuple(f"http://{host}" for host in hosts):
            self.send_error(403, "Origine externe interdite")
            return False
        url = urlparse(self.path)
        callback = (
            self.command == "GET" and url.path == "/" and "state" in parse_qs(url.query)
        )
        if self.headers.get("Sec-Fetch-Site") == "cross-site" and not callback:
            self.send_error(403, "Requête externe interdite")
            return False
        return True

    def log_message(self, *_args):
        pass

    def native_request(self):
        secret = os.environ.get("CINEMA_IMPORT_TOKEN")
        received = self.headers.get("X-Cinema-Import", "")
        return bool(
            secret and secrets.compare_digest(secret.encode(), received.encode())
        )

    def send_json(self, value):
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def download_service_error(self):
        return self.send_json(
            {
                "ok": False,
                "active": False,
                "items": [],
                "error": "Le service de téléchargement est indisponible. Rouvre Fauteuil pour le relancer.",
            }
        )

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/health":
            if not self.native_request():
                return self.send_error(403)
            return self.send_json({"ok": True})
        if url.path.startswith("/api/stream/"):
            return self.send_stream(url.path)
        if url.path == "/api/media":
            return self.send_media(url.query)
        if url.path == "/detail":
            self.path = "/detail.html"
            return super().do_GET()
        if url.path == "/":
            query = parse_qs(url.query)
            if "state" in query:
                return self.google_callback(query)
        endpoint = self.GET_ROUTES.get(url.path)
        if endpoint is not None:
            try:
                return endpoint(self, url)
            except DownloadServiceUnavailable:
                return self.download_service_error()
        return super().do_GET()

    def do_POST(self):
        if self.headers.get_content_type() != "application/json":
            return self.send_error(415)
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 <= length <= 1024 * 1024:
                raise ValueError
            data = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            return self.send_json({"ok": False, "error": "Requête invalide."})
        route = urlparse(self.path).path
        try:
            endpoint = self.POST_ROUTES.get(route)
            if endpoint is not None:
                return endpoint(self, data)
            if route.startswith("/api/playlist"):
                return self.update_playlist(route, data)
            if route in ("/api/play", "/api/download/keep"):
                return self.start_playback_or_download(route, data)
        except DownloadServiceUnavailable:
            return self.download_service_error()
        return self.send_error(404)

    def translate_path(self, path):
        relative = unquote(urlparse(path).path).lstrip("/") or "index.html"
        root = (config.ROOT / "static").resolve()
        target = (root / relative).resolve()
        return str(target if target.is_relative_to(root) else root / ".not-found")
