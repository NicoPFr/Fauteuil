"""Download service process and private HTTP/JSON Unix socket."""

import json
import os
import signal
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler

from fauteuil.downloads import batches, cleanup, jobs
from fauteuil.downloads.state import registry


class WorkerServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class WorkerHandler(BaseHTTPRequestHandler):
    OPERATIONS = {
        "download-status": jobs.download_status,
        "active-stage": jobs.is_active_stage,
        "download": jobs.run_download,
        "play": jobs.progressive_source,
        "prepare-trip": batches.prepare_trip,
        "cancel-trip": batches.cancel_trip,
        "trip-status": batches.trip_status,
        "remove-download": cleanup.remove_managed_download,
        "progress": jobs.record_progress,
    }

    def log_message(self, *_args):
        pass

    def do_POST(self):
        operation = self.OPERATIONS.get(self.path.removeprefix("/"))
        if operation is None:
            return self.send_error(404)
        if self.headers.get_content_type() != "application/json":
            return self.send_error(415)
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 1 <= length <= 1024 * 1024 + 4096:
                raise ValueError
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            return self.send_error(400)

        result = operation(**data)
        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def serve(path):
    with WorkerServer(path, WorkerHandler) as server:

        def quit_server(*_args):
            if registry.closing.is_set():
                return
            registry.closing.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

        def watch_parent():
            # EOF also shuts down the worker if its parent dies without signalling it.
            os.read(sys.stdin.fileno(), 1)
            quit_server()

        signal.signal(signal.SIGTERM, quit_server)
        signal.signal(signal.SIGINT, quit_server)
        threading.Thread(target=watch_parent, daemon=True).start()
        try:
            cleanup.recover_downloads()
            threading.Thread(
                target=cleanup.cleanup_abandoned_downloads, daemon=True
            ).start()
            print("ready", flush=True)
            server.serve_forever()
        finally:
            jobs.stop_downloads()


if __name__ == "__main__":
    serve(sys.argv[1])
