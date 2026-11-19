"""Run the local gateway and supervise the download service for one app session."""

import os
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
from contextlib import ExitStack, contextmanager
from pathlib import Path

from . import config, database
from .download_client import DownloadClient, DownloadServiceUnavailable
from .gateway import GatewayServer


@contextmanager
def download_service():
    # A short path leaves room for the Unix socket address limit on macOS.
    with tempfile.TemporaryDirectory(prefix="fauteuil-", dir="/tmp") as directory:
        socket_path = Path(directory) / "download.sock"
        environment = {
            key: value
            for key, value in os.environ.items()
            if key != "CINEMA_IMPORT_TOKEN"
        }
        with subprocess.Popen(
            [sys.executable, "-m", "fauteuil.download_worker", str(socket_path)],
            cwd=config.ROOT,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        ) as process:
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    if (
                        not selector.select(timeout=10)
                        or process.stdout.readline() != b"ready\n"
                    ):
                        raise DownloadServiceUnavailable(
                            "Le service de téléchargement n’a pas démarré."
                        )
                yield DownloadClient(socket_path)
            finally:
                process.stdin.close()
                try:
                    process.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def serve():
    config.configure_tools()
    with GatewayServer(("127.0.0.1", config.PORT)) as server:
        closing = threading.Event()

        def quit_server(_signum, _frame):
            if not closing.is_set():
                closing.set()
                threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, quit_server)
        signal.signal(signal.SIGINT, quit_server)
        database.initialize()
        with ExitStack() as services:
            try:
                server.downloads = services.enter_context(download_service())
            except (OSError, DownloadServiceUnavailable) as error:
                print(error, file=sys.stderr)
            server.serve_forever()
