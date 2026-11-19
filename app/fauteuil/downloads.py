"""Download jobs, progressive playback, offline batches and cleanup."""

import errno
import json
import math
import os
import pathlib
import re
import secrets
import shutil
import signal
import sqlite3
import stat
import subprocess
import threading
import time

from . import config
from .database import connect, db
from .local_media import local_youtube_file
from .media_index import managed_downloads, playback_source
from .playback import record_progress as save_playback_progress
from .youtube_links import YOUTUBE_STATE_TTL

DOWNLOAD_SCRIPT = config.ROOT / "ytmp4.sh"
if not DOWNLOAD_SCRIPT.is_file():
    DOWNLOAD_SCRIPT = config.ROOT.parent / "ytmp4.sh"
active_downloads = {}
download_groups = {}
playback_downloads = {}
download_lock = threading.Lock()
closing = threading.Event()


def signal_download_group(group, signum):
    try:
        os.killpg(group, signum)
    except ProcessLookupError:
        pass


def stop_downloads():
    for signum, delay in ((signal.SIGTERM, 2), (signal.SIGKILL, 2)):
        with download_lock:
            for group in download_groups:
                signal_download_group(group, signum)
        limit = time.monotonic() + delay
        while time.monotonic() < limit:
            with download_lock:
                if not active_downloads and not download_groups:
                    return
            time.sleep(0.05)


MIN_DOWNLOAD_FREE_BYTES = 5 * 1024**3


def download_status(video_id):
    with download_lock:
        if video_id not in active_downloads:
            job = playback_downloads.get(video_id)
            if (
                job
                and job["event"].is_set()
                and job["result"]
                and not job["result"].get("ok")
            ):
                return {
                    "active": False,
                    "error": job["result"].get("error", "La préparation a échoué."),
                }
            return {"active": False}
        stage = active_downloads[video_id]
    pending = {"active": True, "phase": "preparing"}
    if stage is None:
        return pending
    try:
        stream = json.loads((stage / ".cinema-stream.json").read_text())
        return {
            "active": True,
            "phase": "processing" if stream["complete"] else "downloading",
            "percent": min(100, 100 * stream["seconds"] / stream["duration"]),
        }
    except (OSError, ValueError, KeyError, ZeroDivisionError):
        pass
    try:
        plan = json.loads((stage / ".cinema-formats.json").read_text(encoding="utf-8"))
        with (stage / "logs" / "latest.log").open("rb") as log:
            log.seek(max(0, os.fstat(log.fileno()).st_size - 8192))
            lines = log.read(8192).decode("utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return pending
    formats = [name for name, _ in plan["formats"]]
    weights = [size for _, size in plan["formats"]]
    if not all(weights):
        weights = [1] * len(formats)
    for line in reversed(lines):
        if not line.startswith("CINEMA_MEDIA "):
            continue
        try:
            name, percent, status = line.removeprefix("CINEMA_MEDIA ").rsplit(" ", 2)
            name = json.loads(name)
        except ValueError:
            continue
        if name not in formats and name != plan["combined"]:
            continue
        if status == "finished" and name in (formats[-1], plan["combined"]):
            return {"active": True, "phase": "processing", "percent": 100}
        try:
            number = float(percent)
        except ValueError:
            return {"active": True, "phase": "downloading"}
        if not math.isfinite(number):
            return {"active": True, "phase": "downloading"}
        number = max(0, min(100, number))
        if name in formats:
            index = formats.index(name)
            number = (100 * sum(weights[:index]) + number * weights[index]) / sum(
                weights
            )
        return {"active": True, "phase": "downloading", "percent": min(99.9, number)}
    return pending


JOB_MARKER = ".cinema-download"
ABANDONED_JOB_AGE = 24 * 60 * 60


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
        if closing.is_set():
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


def download_interrupted(cancel=None):
    return closing.is_set() or (cancel is not None and cancel.is_set())


def download_attempt(stage, command, environment, cancel, timeout):
    with download_lock:
        if download_interrupted(cancel):
            return None
        process = subprocess.Popen(
            command,
            env=environment,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
        )
        download_groups[process.pid] = cancel
    try:
        with process:
            try:
                mark_job(stage, process.pid)
                _, stderr = process.communicate(timeout=timeout)
            except (OSError, subprocess.TimeoutExpired):
                signal_download_group(process.pid, signal.SIGKILL)
                process.communicate()
                raise
            return process.returncode, stderr
    finally:
        with download_lock:
            download_groups.pop(process.pid, None)


YOUTUBE_DOWNLOAD_LIMITS = {
    "is_live": "Ce direct est disponible sur YouTube. Attends le replay pour le télécharger.",
    "is_upcoming": "Cette diffusion n’a pas encore commencé.",
    "post_live": "YouTube prépare encore le replay. Réessaie dans quelques minutes.",
    "unavailable": "YouTube ne propose pas de fichier que Fauteuil puisse télécharger pour le moment.",
}


def prepare_download(video_id, keep=False, cancel=None, progressive=False):
    temp_root = config.DATA.resolve() / "À regarder"
    if temp_root.is_symlink():
        return {
            "ok": False,
            "error": "Le dossier de téléchargement de Fauteuil doit être un dossier local.",
        }
    cached = next(managed_downloads(video_id), None)
    if cached:
        if not keep or cached["kept"]:
            return dict(
                playback_source(cached["path"], temporary=not cached["kept"]),
                kept=bool(cached["kept"]),
                local=False,
            )
        with connect() as connection:
            pinned = connection.execute(
                "update temporary_downloads set kept=1,created=? where id=? and path=?",
                (time.time(), video_id, str(cached["path"])),
            ).rowcount
        if pinned:
            return dict(playback_source(cached["path"]), kept=True, local=False)
    state = db(
        "select youtube_state from video_synopses where id=? and fetched_at>?",
        (video_id, time.time() - YOUTUBE_STATE_TTL),
    )
    if state and state[0]["youtube_state"] in YOUTUBE_DOWNLOAD_LIMITS:
        return {
            "ok": False,
            "error": YOUTUBE_DOWNLOAD_LIMITS[state[0]["youtube_state"]],
        }
    if not DOWNLOAD_SCRIPT.is_file():
        return {
            "ok": False,
            "error": "Le téléchargement est momentanément indisponible. Réessaie plus tard.",
        }
    if shutil.disk_usage(config.DATA).free < MIN_DOWNLOAD_FREE_BYTES:
        return {
            "ok": False,
            "error": "Espace disque insuffisant. Fauteuil garde au moins 5 Gio libres sur le Mac.",
        }

    stage = temp_root / secrets.token_hex(8)
    stage.mkdir(parents=True)
    with download_lock:
        if video_id in active_downloads:
            active_downloads[video_id] = stage
        if progressive and video_id in playback_downloads:
            playback_downloads[video_id]["stage"] = stage
    keep_stage = False
    try:
        mark_job(stage)
        environment = dict(
            os.environ,
            YT_MOVIES_DIR=str(stage),
            YT_CINEMA_PROGRESS="1",
            YT_CINEMA_RESERVE_BYTES=str(MIN_DOWNLOAD_FREE_BYTES),
        )
        if progressive:
            environment["YT_CINEMA_STREAM"] = "1"
        command = [
            str(DOWNLOAD_SCRIPT),
            "--no-archive",
            "--fr-subs",
            "https://www.youtube.com/watch?v=" + video_id,
        ]
        deadline = time.monotonic() + config.DOWNLOAD_TIMEOUT
        attempts = 0
        while attempts < 2:
            attempts += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {
                    "ok": False,
                    "error": "Le téléchargement a dépassé le délai autorisé. Tu peux réessayer.",
                }
            try:
                result = download_attempt(
                    stage, command, environment, cancel, remaining
                )
            except subprocess.TimeoutExpired:
                return {
                    "ok": False,
                    "error": "Le téléchargement a dépassé le délai autorisé. Tu peux réessayer.",
                }
            if result is None or download_interrupted(cancel):
                return {"ok": False, "error": "Téléchargement interrompu."}
            return_code, stderr = result
            if return_code == 0:
                break
            if progressive and not re.search(
                r"CINEMA_DISK_REQUIRED|no space left on device|errno 28", stderr, re.I
            ):
                # The standard downloader handles formats and endpoints FFmpeg cannot stream.
                progressive = False
                environment.pop("YT_CINEMA_STREAM", None)
                shutil.rmtree(stage / "stream", ignore_errors=True)
                (stage / ".cinema-stream.json").unlink(missing_ok=True)
                continue
            if "HTTP Error 403" not in stderr:
                break
        if return_code:
            disk_estimate = re.search(r"CINEMA_DISK_REQUIRED (\d+) (\d+)", stderr)
            if disk_estimate:
                unit = 1024**3
                required, available = (
                    float(value) / unit for value in disk_estimate.groups()
                )
                message = (
                    f"Espace insuffisant avant le téléchargement. Prévois environ {required:.2f} Gio "
                    f"pour le fichier et son assemblage. Il reste {available:.2f} Gio."
                )
                return {"ok": False, "error": message}
            if (
                "no space left on device" in stderr.lower()
                or "errno 28" in stderr.lower()
            ):
                return {
                    "ok": False,
                    "error": "Le téléchargement s’est arrêté faute d’espace disque disponible.",
                }
            if "HTTP Error 403" in stderr:
                return {
                    "ok": False,
                    "error": "YouTube a encore refusé le téléchargement après une nouvelle tentative (erreur 403).",
                }
            return {
                "ok": False,
                "error": "Le téléchargement a échoué. Vérifie ta connexion puis réessaie.",
            }
        candidates = [
            p
            for p in (stage / "Movies").rglob("*.mp4")
            if p.is_file() and p.stat().st_size > 0
        ]
        if not candidates:
            try:
                log = (stage / "logs" / "latest.log").read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                log = ""
            if "does not pass filter (live_status" in log:
                return {
                    "ok": False,
                    "error": "Attends que le replay soit disponible sur YouTube avant de télécharger cette vidéo.",
                }
            return {
                "ok": False,
                "error": "Le téléchargement n’a produit aucun fichier MP4.",
            }
        path = max(candidates, key=lambda p: p.stat().st_mtime).resolve()
        db(
            "insert or replace into temporary_downloads(id,path,created,kept) values(?,?,?,?)",
            (video_id, str(path), time.time(), int(keep)),
            write=True,
        )
        keep_stage = True
        shutil.rmtree(stage / "logs", ignore_errors=True)
        try:
            (stage / ".cinema-formats.json").unlink(missing_ok=True)
        except OSError:
            pass
        return dict(playback_source(path, temporary=not keep), kept=keep, local=False)
    finally:
        if not keep_stage:
            shutil.rmtree(stage, ignore_errors=True)


TRASH_COMMAND = "/usr/bin/trash"


def remove_managed_download(video_id):
    with download_lock:
        if video_id in active_downloads:
            return {"ok": False, "error": "Cette vidéo est en cours de préparation."}
        active_downloads[video_id] = None
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
        with download_lock:
            active_downloads.pop(video_id, None)


def retry_playback_cleanup(video_id=None):
    # Keep the request and the catalogue row until the filesystem operation succeeds.
    with download_lock:
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
                    if ident in active_downloads:
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


def run_download(video_id, keep=False, cancel=None, progressive=False):
    if download_interrupted(cancel):
        return {"ok": False, "error": "Téléchargement interrompu."}
    local = local_youtube_file(video_id)
    if local is not None:
        return dict(playback_source(local), id="local:" + str(local))
    with download_lock:
        if video_id in active_downloads:
            return {
                "ok": False,
                "error": "Cette vidéo est déjà en cours de téléchargement.",
            }
        active_downloads[video_id] = None
    try:
        return prepare_download(
            video_id, keep=keep, cancel=cancel, progressive=progressive
        )
    except OSError as error:
        message = (
            "L’espace disque est insuffisant."
            if error.errno == errno.ENOSPC
            else "Fauteuil n’a pas pu enregistrer le fichier."
        )
        return {"ok": False, "error": message}
    except sqlite3.Error:
        return {
            "ok": False,
            "error": "Fauteuil n’a pas pu enregistrer le téléchargement dans sa base.",
        }
    finally:
        with download_lock:
            active_downloads.pop(video_id, None)
        retry_playback_cleanup(video_id)


def progressive_source(video_id, start=0, wait_for_file=False):
    # Repeated clicks join the same job. The request returns after two complete segments.
    local = local_youtube_file(video_id)
    if local is not None:
        return dict(playback_source(local), id="local:" + str(local))
    cached = next(managed_downloads(video_id), None)
    if cached:
        return dict(
            playback_source(cached["path"], temporary=not cached["kept"]), local=False
        )
    with download_lock:
        job = playback_downloads.get(video_id)
        if job is None or job["event"].is_set():
            if video_id in active_downloads:
                return {
                    "ok": False,
                    "error": "Cette vidéo est déjà en préparation pour le hors ligne. Réessaie à la fin du téléchargement.",
                }
            for old_id, old in list(playback_downloads.items()):
                if old["event"].is_set() and (
                    old["detached"] or not old["result"].get("ok")
                ):
                    playback_downloads.pop(old_id, None)
            job = {
                "event": threading.Event(),
                "result": None,
                "detached": False,
                "stage": None,
            }
            playback_downloads[video_id] = job

            def worker():
                try:
                    job["result"] = run_download(video_id, progressive=True)
                except Exception:
                    job["result"] = {
                        "ok": False,
                        "error": "La préparation de la vidéo a échoué. Réessaie.",
                    }
                finally:
                    try:
                        stage = job["stage"]
                        if stage and job["detached"]:
                            shutil.rmtree(stage / "stream", ignore_errors=True)
                    finally:
                        job["event"].set()

            threading.Thread(target=worker, daemon=True).start()
        job["detached"] = False
    try:
        start = max(0, float(start))
    except (TypeError, ValueError):
        start = 0
    if not math.isfinite(start):
        start = 0
    while not closing.is_set():
        if job["event"].wait(0.2):
            return job["result"]
        if wait_for_file:
            continue
        with download_lock:
            stage = active_downloads.get(video_id)
        if stage is None:
            continue
        job["stage"] = stage
        try:
            state = json.loads((stage / ".cinema-stream.json").read_text())
            manifest = (stage / "stream/index.m3u8").read_text()
            buffered = sum(float(n) for n in re.findall(r"#EXTINF:([0-9.]+)", manifest))
            if buffered < min(state["duration"], start + 8):
                continue
            return {
                "ok": True,
                "path": state["path"],
                "temporary": True,
                "local": False,
                "progressive": True,
                "duration": state["duration"],
                "media_url": f"/api/stream/{stage.name}/index.m3u8",
            }
        except (OSError, ValueError, KeyError):
            continue
    return {"ok": False, "error": "La préparation a été interrompue."}


def stop_batch_downloads(cancel):
    def signal_matching_groups(signum):
        with download_lock:
            for group, owner in download_groups.items():
                if owner is cancel:
                    signal_download_group(group, signum)

    signal_matching_groups(signal.SIGTERM)
    force = threading.Timer(2, signal_matching_groups, args=(signal.SIGKILL,))
    force.daemon = True
    force.start()


def is_active_stage(stage):
    with download_lock:
        return config.DOWNLOAD_ROOT.resolve() / stage in active_downloads.values()


def recover_downloads():
    recover_finished_downloads()
    for entry in managed_downloads():
        stage = entry["path"].relative_to(config.DOWNLOAD_ROOT.resolve()).parts[0]
        if re.fullmatch(r"[0-9a-f]{16}", stage):
            shutil.rmtree(config.DOWNLOAD_ROOT / stage / "stream", ignore_errors=True)


def record_progress(data, profile):
    ident = data.get("id")
    if not isinstance(ident, str):
        return {"ok": False, "error": "Téléchargement introuvable."}
    with download_lock:
        job = playback_downloads.get(ident)
        stage = job["stage"] if job else None
    stream = None
    if stage:
        try:
            state = json.loads((stage / ".cinema-stream.json").read_text())
            if (
                pathlib.Path(state["path"]).resolve()
                == pathlib.Path(data["path"]).resolve()
            ):
                stream = state
        except (OSError, TypeError, ValueError, KeyError):
            pass
    if stream and (stream.get("input_complete") or stream.get("complete")):
        try:
            manifest = (stage / "stream/index.m3u8").read_text()
            if "#EXT-X-ENDLIST" in manifest:
                duration = sum(
                    float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", manifest)
                )
                if duration > 0:
                    stream["duration"] = duration
        except OSError:
            pass
    result = save_playback_progress(data, profile, stream)
    if result.get("ok"):
        if stage and (data.get("closing") is True or result["completed"]):
            with download_lock:
                job["detached"] = True
                if job["event"].is_set():
                    shutil.rmtree(stage / "stream", ignore_errors=True)
        if result["completed"]:
            retry_playback_cleanup(ident)
    return result
