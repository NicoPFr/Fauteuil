"""Loopback gateway harness using a disposable catalogue and isolated job state."""

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fauteuil import config
from fauteuil.downloads import batches, jobs
from fauteuil.downloads.state import registry
from fauteuil.http.server import GatewayServer
from fauteuil.storage import database


class GatewayTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fauteuil-gateway-test-")
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
                registry,
                active={},
                playback={},
                process_groups={},
            )
        )
        self.enterContext(
            patch.object(
                batches,
                "trip",
                {"profile": None, "active": False, "items": []},
            )
        )
        database.initialize()
        service = SimpleNamespace(
            download_status=jobs.download_status,
            record_progress=jobs.record_progress,
            prepare_trip=batches.prepare_trip,
            trip_status=batches.trip_status,
        )
        self.server = GatewayServer(("127.0.0.1", 0), service)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, body=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.server.server_port}" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)
