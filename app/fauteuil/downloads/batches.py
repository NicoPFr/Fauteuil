"""Offline download queues, scoped to the profile that created each batch."""

import re
import sqlite3
import threading

from fauteuil.downloads import jobs
from fauteuil.storage.database import db

trip_lock = threading.Lock()
trip = {"profile": None, "active": False, "items": []}
TRIP_LIMIT = 20


def prepare_trip(profile, selection, allowed=None):
    if not isinstance(selection, list) or not 1 <= len(selection) <= TRIP_LIMIT:
        return {"ok": False, "error": "Sélectionne entre une et vingt vidéos."}
    if any(
        not isinstance(video_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id)
        for video_id in selection
    ):
        return {
            "ok": False,
            "error": "La sélection contient un titre non téléchargeable.",
        }
    saved = (
        allowed
        if allowed is not None
        else {
            row["id"]: row["title"]
            for row in db(
                "select id,title from watchlist_profile where profile=?", (profile,)
            )
        }
    )
    if any(video_id not in saved for video_id in selection):
        return {
            "ok": False,
            "error": "Ajoute ces vidéos à Ma liste avant de les sélectionner.",
        }
    entries = [
        {"id": video_id, "title": saved[video_id], "state": "pending"}
        for video_id in dict.fromkeys(selection)
    ]
    with trip_lock:
        if trip["active"]:
            return {
                "ok": False,
                "error": "Une sélection est déjà en cours de préparation.",
            }
        cancel = threading.Event()
        trip.update(profile=profile, active=True, items=entries, cancel=cancel)

    def worker():
        try:
            for entry in entries:
                with trip_lock:
                    if jobs.download_interrupted(cancel):
                        break
                    entry["state"] = "downloading"
                try:
                    result = jobs.run_download(entry["id"], keep=True, cancel=cancel)
                except (OSError, sqlite3.Error, ValueError):
                    result = {
                        "ok": False,
                        "error": "Impossible de préparer cette vidéo.",
                    }
                with trip_lock:
                    if result.get("ok"):
                        entry["state"] = "ready"
                    elif cancel.is_set():
                        entry["state"] = "cancelled"
                    else:
                        entry.update(
                            state="failed",
                            error=result.get("error", "Téléchargement échoué."),
                        )
        finally:
            with trip_lock:
                for entry in entries:
                    if entry["state"] in ("pending", "downloading"):
                        entry["state"] = "cancelled"
                trip["active"] = False

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True}


def cancel_trip(profile):
    with trip_lock:
        if trip["profile"] != profile or not trip["active"]:
            return {"ok": False, "error": "Aucune préparation en cours pour ce profil."}
        cancel = trip["cancel"]
        if cancel.is_set():
            return {"ok": True}
        cancel.set()

    jobs.stop_batch_downloads(cancel)
    return {"ok": True}


def trip_status(profile):
    with trip_lock:
        if trip["profile"] != profile:
            return {"active": False, "items": []}
        status = {
            "active": trip["active"],
            "cancelling": trip["active"] and trip["cancel"].is_set(),
            "items": [dict(entry) for entry in trip["items"]],
        }
    for entry in status["items"]:
        if entry["state"] == "downloading":
            entry["progress"] = jobs.download_status(entry["id"])
    return status
