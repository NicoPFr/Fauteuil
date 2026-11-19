"""Download staging recovery and safe removal of managed files."""

import json
import os
import pathlib
import re
import shutil
import sqlite3
import stat
import subprocess
import time

from fauteuil import config
from fauteuil.downloads.state import registry
from fauteuil.media.index import managed_downloads
from fauteuil.storage.database import connect, db

JOB_MARKER = ".cinema-download"
ABANDONED_JOB_AGE = 24 * 60 * 60
TRASH_COMMAND = "/usr/bin/trash"


def mark_job(stage, group=None):
    marker = stage / JOB_MARKER
    marker.write_text(
        json.dumps({"server": os.getpid(), "group": group}), encoding="utf-8"
    )


def job_process_exists(pid, group=False):
    if not isinstance(pid, int) or not 1 < pid < 2**31:
        return False
    try:
        (os.killpg if group else os.kill)(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def cleanup_abandoned_downloads():
    """Sweep only old, unregistered staging directories with no live owner."""
    root = config.DATA.resolve() / "À regarder"
    if root.is_symlink() or not root.is_dir():
        return
    cutoff = time.time() - ABANDONED_JOB_AGE
    try:
        registered = {
            pathlib.Path(row["path"]).resolve()
            for row in db("select path from temporary_downloads")
        }
        folders = list(root.iterdir())
    except (OSError, RuntimeError, sqlite3.Error):
        return
    for folder in folders:
        if registry.closing.is_set():
            return
        if (
            not re.fullmatch(r"[0-9a-f]{16}", folder.name)
            or folder.is_symlink()
            or not folder.is_dir()
        ):
            continue
        if any(folder in path.parents for path in registered):
            continue
        marker = folder / JOB_MARKER
        try:
            info = marker.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mtime >= cutoff
                or info.st_size > 1024
            ):
                continue
            owner = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(owner, dict) or not isinstance(owner.get("server"), int):
                continue
            if job_process_exists(owner.get("server")) or job_process_exists(
                owner.get("group"), group=True
            ):
                continue
            shutil.rmtree(folder)
        except (OSError, ValueError):
            continue


def remove_managed_download(video_id):
    with registry.lock:
        if video_id in registry.active:
            return {"ok": False, "error": "Cette vidéo est en cours de préparation."}
        registry.active[video_id] = None
    try:
        with connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                "select path from temporary_downloads where id=?", (video_id,)
            ).fetchone()
            if row is None:
                return {"ok": False, "error": "Aucun téléchargement pour cette vidéo."}
            root = config.DATA.resolve() / "À regarder"
            path = pathlib.Path(row[0]).resolve()
            if root.is_symlink() or root not in path.parents:
                return {
                    "ok": False,
                    "error": "Emplacement de téléchargement non reconnu.",
                }
            parts = path.relative_to(root).parts
            if (
                len(parts) < 3
                or parts[1] != "Movies"
                or not re.fullmatch(r"[0-9a-f]{16}", parts[0])
            ):
                return {
                    "ok": False,
                    "error": "Ce téléchargement provient d’une ancienne version de Fauteuil.",
                }
            folder = root / parts[0]
            others = connection.execute(
                "select path from temporary_downloads where id<>?", (video_id,)
            ).fetchall()
            if any(
                folder in pathlib.Path(other[0]).resolve().parents for other in others
            ):
                return {
                    "ok": False,
                    "error": "Ce dossier contient aussi un autre téléchargement.",
                }
            if folder.exists():
                if not shutil.which(TRASH_COMMAND):
                    return {
                        "ok": False,
                        "error": "La commande de corbeille macOS est indisponible sur ce Mac.",
                    }
                try:
                    subprocess.run(
                        [TRASH_COMMAND, "-s", str(folder)],
                        capture_output=True,
                        check=True,
                        timeout=4,
                    )
                except (OSError, subprocess.SubprocessError):
                    return {
                        "ok": False,
                        "error": "macOS n’a pas pu déplacer ce téléchargement vers la Corbeille.",
                    }
            connection.execute(
                "delete from temporary_downloads where id=?", (video_id,)
            )
        return {"ok": True}
    except (OSError, sqlite3.Error):
        return {"ok": False, "error": "Le téléchargement n’a pas pu être retiré."}
    finally:
        with registry.lock:
            registry.active.pop(video_id, None)


def retry_playback_cleanup(video_id=None):
    """Recheck the exact registered path and retention flag before deleting."""
    with registry.lock:
        root = config.DATA.resolve() / "À regarder"
        if root.is_symlink():
            return
        condition = " where id=?" if video_id is not None else ""
        args = (video_id,) if video_id is not None else ()
        try:
            with connect() as connection:
                connection.execute("begin immediate")
                for ident, filename in connection.execute(
                    "select id,path from pending_playback_cleanup" + condition, args
                ).fetchall():
                    if ident in registry.active:
                        continue
                    row = connection.execute(
                        "select kept from temporary_downloads where id=? and path=?",
                        (ident, filename),
                    ).fetchone()
                    path = pathlib.Path(filename)
                    if (
                        row
                        and not row[0]
                        and root in path.parents
                        and path.resolve() == path
                    ):
                        relative = path.relative_to(root).parts
                        try:
                            others = connection.execute(
                                "select path from temporary_downloads where id<>? or path<>?",
                                (ident, filename),
                            )
                            folder = root / relative[0]
                            other_paths = [
                                pathlib.Path(other[0]).resolve() for other in others
                            ]
                            if path in other_paths:
                                continue
                            if (
                                len(relative) >= 3
                                and relative[1] == "Movies"
                                and re.fullmatch(r"[0-9a-f]{16}", relative[0])
                                and not any(
                                    folder in other.parents for other in other_paths
                                )
                            ):
                                if folder.exists():
                                    shutil.rmtree(folder)
                            else:
                                path.unlink(missing_ok=True)
                        except OSError:
                            # Keep both database rows so a later call can retry the deletion.
                            continue
                        connection.execute(
                            "delete from temporary_downloads where id=? and path=? and kept=0",
                            (ident, filename),
                        )
                    connection.execute(
                        "delete from pending_playback_cleanup where id=? and path=?",
                        (ident, filename),
                    )
        except sqlite3.Error:
            # The durable request can be retried at the next launch.
            return


def recover_finished_downloads():
    # Repair older copies when the history proves they were watched after this download.
    db(
        """insert or ignore into pending_playback_cleanup
       select d.id,d.path from temporary_downloads d
       where d.kept=0 and exists (select 1 from completed_playback h
                                  where h.id=d.id and h.finished_at>=d.created)
       and not exists (select 1 from playback_profile p where p.id=d.id and p.updated>=d.created)""",
        write=True,
    )
    retry_playback_cleanup()


def recover_downloads():
    recover_finished_downloads()
    for entry in managed_downloads():
        stage = entry["path"].relative_to(config.DOWNLOAD_ROOT.resolve()).parts[0]
        if re.fullmatch(r"[0-9a-f]{16}", stage):
            shutil.rmtree(config.DOWNLOAD_ROOT / stage / "stream", ignore_errors=True)
