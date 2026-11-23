"""Start the actual worker, including under the minimum supported Python."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fauteuil import config
from fauteuil.runtime import download_service
from fauteuil.storage import database


class DownloadWorkerTest(unittest.TestCase):
    def test_worker_answers_on_a_fresh_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.multiple(
                config,
                DATA=root,
                DB=root / "catalogue.sqlite3",
                DOWNLOAD_ROOT=root / "À regarder",
            ), patch.dict(os.environ, CINEMA_DATA_DIR=directory):
                database.initialize()
                with download_service() as worker:
                    self.assertEqual(worker.download_status("abcdefghijk"), {"active": False})
