"""Typed calls to the download process over its private Unix socket."""

import http.client
import json
import socket

from .config import DOWNLOAD_TIMEOUT


class DownloadServiceUnavailable(RuntimeError):
    pass


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self.path = str(path)

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(self.path)
        except OSError:
            sock.close()
            raise
        self.sock = sock


class DownloadClient:
    def __init__(self, path):
        self.path = path

    def _call(self, operation, *, timeout=7, **data):
        if self.path is None:
            raise DownloadServiceUnavailable(operation)
        connection = UnixHTTPConnection(self.path, timeout)
        try:
            connection.request(
                "POST",
                "/" + operation,
                json.dumps(data).encode(),
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            if response.status != 200:
                raise DownloadServiceUnavailable(operation)
            return json.load(response)
        except (OSError, ValueError, http.client.HTTPException) as error:
            raise DownloadServiceUnavailable(operation) from error
        finally:
            connection.close()

    def download_status(self, video_id):
        return self._call("download-status", video_id=video_id)

    def is_active_stage(self, stage):
        return self._call("active-stage", stage=stage)

    def run_download(self, video_id, *, keep=False):
        return self._call(
            "download", timeout=DOWNLOAD_TIMEOUT + 30, video_id=video_id, keep=keep
        )

    def progressive_source(self, video_id, *, start=0, wait_for_file=False):
        return self._call(
            "play",
            timeout=DOWNLOAD_TIMEOUT + 30,
            video_id=video_id,
            start=start,
            wait_for_file=wait_for_file,
        )

    def prepare_trip(self, profile, selection, *, allowed=None):
        return self._call(
            "prepare-trip", profile=profile, selection=selection, allowed=allowed
        )

    def cancel_trip(self, profile):
        return self._call("cancel-trip", profile=profile)

    def trip_status(self, profile):
        return self._call("trip-status", profile=profile)

    def remove_managed_download(self, video_id):
        return self._call("remove-download", timeout=15, video_id=video_id)

    def record_progress(self, data, profile):
        return self._call("progress", timeout=15, data=data, profile=profile)
