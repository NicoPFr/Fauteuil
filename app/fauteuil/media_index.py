"""Read-only access to the managed media registry and media URLs."""

import pathlib
import urllib.parse

from . import config
from .database import db


def playback_source(path, temporary=False):
    return {
        "ok": True,
        "path": str(path),
        "temporary": temporary,
        "local": not temporary,
        "media_url": "/api/media?path=" + urllib.parse.quote(str(path)),
    }


def managed_downloads(video_id=None):
    suffix = " where id=?" if video_id is not None else ""
    rows = db(
        "select id,path,kept,created from temporary_downloads" + suffix,
        (video_id,) if video_id is not None else (),
    )
    root = (config.DATA / "À regarder").resolve()
    for row in rows:
        path = pathlib.Path(row["path"]).resolve()
        if root in path.parents and path.is_file():
            yield dict(row, path=path)
