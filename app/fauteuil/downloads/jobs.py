"""Download execution, status and progressive playback coordination."""

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
import subprocess
import threading
import time

from fauteuil import config
from fauteuil.downloads.cleanup import mark_job, retry_playback_cleanup
from fauteuil.downloads.state import registry
from fauteuil.media.index import managed_downloads, playback_source
from fauteuil.media.library import local_youtube_file
from fauteuil.media.progress import record_progress as save_playback_progress
from fauteuil.storage.database import connect, db
from fauteuil.youtube.links import YOUTUBE_STATE_TTL


def signal_download_group(group, signum):
    try:
        os.killpg(group, signum)
    except ProcessLookupError:
        pass


def stop_downloads():
    for signum, delay in ((signal.SIGTERM, 2), (signal.SIGKILL, 2)):
        with registry.lock:
            for group in registry.process_groups:
                signal_download_group(group, signum)
        limit = time.monotonic() + delay
        while time.monotonic() < limit:
            with registry.lock:
                if not registry.active and not registry.process_groups:
                    return
            time.sleep(0.05)


MIN_DOWNLOAD_FREE_BYTES = 5 * 1024**3


def download_status(video_id):
    with registry.lock:
        if video_id not in registry.active:
            job = registry.playback.get(video_id)
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
        stage = registry.active[video_id]
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


def download_interrupted(cancel=None):
    return registry.closing.is_set() or (cancel is not None and cancel.is_set())


def download_attempt(stage, command, environment, cancel, timeout):
    with registry.lock:
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
        registry.process_groups[process.pid] = cancel
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
        with registry.lock:
            registry.process_groups.pop(process.pid, None)


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
    if not config.DOWNLOADER_SCRIPT.is_file():
        return {
            "ok": False,
            "error": "Le téléchargement est momentanément indisponible. Réessaie plus tard.",
        }
    missing_tools = [tool for tool in ("yt-dlp", "ffmpeg") if not shutil.which(tool)]
    if missing_tools:
        return {
            "ok": False,
            "error": (
                "Outils de téléchargement manquants (" + ", ".join(missing_tools)
                + "). Installe-les avec Homebrew, puis relance Fauteuil."
            ),
        }
    if shutil.disk_usage(config.DATA).free < MIN_DOWNLOAD_FREE_BYTES:
        return {
            "ok": False,
            "error": "Espace disque insuffisant. Fauteuil garde au moins 5 Gio libres sur le Mac.",
        }

    stage = temp_root / secrets.token_hex(8)
    stage.mkdir(parents=True)
    with registry.lock:
        if video_id in registry.active:
            registry.active[video_id] = stage
        if progressive and video_id in registry.playback:
            registry.playback[video_id]["stage"] = stage
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
            str(config.DOWNLOADER_SCRIPT),
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


def run_download(video_id, keep=False, cancel=None, progressive=False):
    if download_interrupted(cancel):
        return {"ok": False, "error": "Téléchargement interrompu."}
    local = local_youtube_file(video_id)
    if local is not None:
        return dict(playback_source(local), id="local:" + str(local))
    with registry.lock:
        if video_id in registry.active:
            return {
                "ok": False,
                "error": "Cette vidéo est déjà en cours de téléchargement.",
            }
        registry.active[video_id] = None
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
        with registry.lock:
            registry.active.pop(video_id, None)
        retry_playback_cleanup(video_id)


def progressive_source(video_id, start=0, wait_for_file=False):
    """Join an existing job and return a playable HLS source or the finished MP4."""
    local = local_youtube_file(video_id)
    if local is not None:
        return dict(playback_source(local), id="local:" + str(local))
    cached = next(managed_downloads(video_id), None)
    if cached:
        return dict(
            playback_source(cached["path"], temporary=not cached["kept"]), local=False
        )
    with registry.lock:
        job = registry.playback.get(video_id)
        if job is None or job["event"].is_set():
            if video_id in registry.active:
                return {
                    "ok": False,
                    "error": "Cette vidéo est déjà en préparation pour le hors ligne. Réessaie à la fin du téléchargement.",
                }
            for old_id, old in list(registry.playback.items()):
                if old["event"].is_set() and (
                    old["detached"] or not old["result"].get("ok")
                ):
                    registry.playback.pop(old_id, None)
            job = {
                "event": threading.Event(),
                "result": None,
                "detached": False,
                "stage": None,
            }
            registry.playback[video_id] = job

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
    while not registry.closing.is_set():
        if job["event"].wait(0.2):
            return job["result"]
        if wait_for_file:
            continue
        with registry.lock:
            stage = registry.active.get(video_id)
        if stage is None:
            continue
        job["stage"] = stage
        try:
            state = json.loads((stage / ".cinema-stream.json").read_text())
            manifest = (stage / "stream/index.m3u8").read_text()
            buffered = sum(float(n) for n in re.findall(r"#EXTINF:([0-9.]+)", manifest))
            # A resume request needs a buffer after the saved position, capped by EOF.
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
        with registry.lock:
            for group, owner in registry.process_groups.items():
                if owner is cancel:
                    signal_download_group(group, signum)

    signal_matching_groups(signal.SIGTERM)
    force = threading.Timer(2, signal_matching_groups, args=(signal.SIGKILL,))
    force.daemon = True
    force.start()


def is_active_stage(stage):
    with registry.lock:
        return config.DOWNLOAD_ROOT.resolve() / stage in registry.active.values()


def record_progress(data, profile):
    """Check the duration of an active stream before committing its progress."""
    ident = data.get("id")
    if not isinstance(ident, str):
        return {"ok": False, "error": "Téléchargement introuvable."}
    with registry.lock:
        job = registry.playback.get(ident)
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
    # A partial manifest describes the current buffer. The final one can refine
    # the duration reported by the extractor.
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
            with registry.lock:
                job["detached"] = True
                if job["event"].is_set():
                    shutil.rmtree(stage / "stream", ignore_errors=True)
        if result["completed"]:
            retry_playback_cleanup(ident)
    return result
