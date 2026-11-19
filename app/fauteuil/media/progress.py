"""Shared playback position transactions for local and downloaded media."""

import math
import pathlib
import time

from fauteuil import config
from fauteuil.media.library import is_local_video
from fauteuil.storage.database import connect


def record_progress(data, profile, stream=None):
    """Atomically record a position or completion and any pending cleanup."""
    try:
        path = pathlib.Path(data.get("path", "")).resolve()
        ident = data.get("id", "local:" + str(path))
        current = float(data.get("time") or 0)
        duration = float(stream["duration"] if stream else data.get("duration") or 0)
    except (OSError, TypeError, ValueError):
        return {
            "ok": False,
            "error": "Position de lecture invalide.",
            "completed": False,
        }

    temporary = config.DOWNLOAD_ROOT.resolve() in path.parents
    if (not path.is_file() and not stream) or (
        not temporary and (not is_local_video(path) or ident != "local:" + str(path))
    ):
        return {"ok": False, "error": "Vidéo introuvable."}
    if (
        not math.isfinite(current)
        or not math.isfinite(duration)
        or current < 0
        or duration <= 0
    ):
        return {"ok": False, "error": "Position de lecture invalide."}

    # WebKit can emit ended at the end of an incomplete HLS buffer.
    ended = data.get("ended") is True and (not stream or current >= duration - 2)
    finished_on_close = data.get("closing") is True and duration - current < 30
    done = ended or finished_on_close
    now = time.time()
    with connect() as connection:
        # Read the retention flag under the same write lock as the cleanup request.
        connection.execute("begin immediate")
        if temporary:
            download = connection.execute(
                "select kept from temporary_downloads where id=? and path=?",
                (ident, str(path)),
            ).fetchone()
            if not download and not stream:
                return {"ok": False, "error": "Téléchargement introuvable."}
            if done and (not download or not download[0]):
                connection.execute(
                    "insert or ignore into pending_playback_cleanup values(?,?)",
                    (ident, str(path)),
                )
        if done:
            connection.execute(
                "delete from playback_profile where profile=? and id=?",
                (profile, ident),
            )
            connection.execute(
                "insert or replace into completed_playback values(?,?,?)",
                (profile, ident, now),
            )
        else:
            connection.execute(
                "insert or replace into playback_profile values(?,?,?,?,?,?)",
                (profile, ident, str(path), current, duration, now),
            )
    return {"ok": True, "completed": done}
