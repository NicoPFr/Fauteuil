"""Build library and watchlist views for one profile."""

import re
import urllib.parse

from fauteuil.catalogue.metadata import genre_label
from fauteuil.media.index import managed_downloads
from fauteuil.media.library import (
    display_title,
    embedded_youtube_id,
    local_catalogue,
    local_cover_url,
    series_identifier,
    video_metadata,
)
from fauteuil.storage.database import db


def library_items(profile):
    downloads = {entry["id"]: entry for entry in managed_downloads()}
    saved = {
        r["id"]
        for r in db("select id from watchlist_profile where profile=?", (profile,))
    }
    progress = {
        r["id"]: dict(r)
        for r in db(
            "select id,current,duration,updated from playback_profile where profile=?",
            (profile,),
        )
    }
    finished = {
        row["id"]: row["finished_at"]
        for row in db(
            "select id,finished_at from completed_playback where profile=?",
            (profile,),
        )
    }
    items = [
        dict(x)
        for x in db("select * from videos order by title")
        if int(x["duration"] or 0) > 180
        and not re.search(r"#shorts|\bshorts\b", x["title"] or "", re.I)
    ]
    for x in items:
        x["thumbnail"] = x.get("thumb", "")
        x["watchlisted"] = x["id"] in saved
        x["progress"] = progress.get(x["id"])
        x["finished_at"] = finished.get(x["id"])
    known = {item["id"] for item in items}
    for video_id, entry in downloads.items():
        if video_id in known:
            continue
        metadata = video_metadata(entry["path"])
        items.append(
            {
                "id": video_id,
                "title": display_title(entry["path"]),
                "duration": int(metadata["duration"]),
                "channel": metadata["tags"].get("artist") or "YouTube",
                "genre": genre_label(metadata["tags"].get("genre")),
                "watchlisted": video_id in saved,
                "progress": progress.get(video_id),
                "finished_at": finished.get(video_id),
            }
        )
    for item in items:
        entry = downloads.get(item["id"])
        if entry:
            item["downloaded"] = True
            item["kept"] = bool(entry["kept"])
            if entry["kept"]:
                item["added_at"] = entry["created"]
            cover = entry["path"].with_suffix(".jpg")
            if cover.is_file():
                item["thumbnail"] = "/api/thumb?path=" + urllib.parse.quote(str(cover))
    imported = {
        row["path"]: row["added_at"]
        for row in db("select path,added_at from imported_videos")
    }
    for path, series_root, season in local_catalogue():
        try:
            file_stat = path.stat()
        except OSError:
            continue
        added_at = imported.get(
            str(path), getattr(file_stat, "st_birthtime", file_stat.st_mtime)
        )
        metadata = video_metadata(path)
        video_id = "local:" + str(path)
        items.append(
            {
                "id": video_id,
                "title": display_title(path),
                "thumbnail": local_cover_url(path),
                "duration": int(metadata["duration"]),
                "channel": metadata["tags"].get("artist") or "Sur cet appareil",
                "genre": genre_label(metadata["tags"].get("genre")),
                "local": True,
                "path": str(path),
                "source_id": embedded_youtube_id(metadata["tags"]),
                "added_at": added_at,
                "collection": (
                    series_root.name.replace("_", " ") if series_root else "Films"
                ),
                "series_id": (series_identifier(series_root) if series_root else None),
                "season": "Saison " + str(season) if season else "",
                "season_number": season,
                "watchlisted": video_id in saved,
                "progress": progress.get(video_id),
                "finished_at": finished.get(video_id),
            }
        )
    return items


def watchlist_items(profile):
    rows = [
        dict(x)
        for x in db(
            "select id,title,thumb,duration,channel,added from watchlist_profile where profile=? order by added desc",
            (profile,),
        )
        if int(x["duration"] or 0) > 180
        and not re.search(r"#shorts|\bshorts\b", x["title"] or "", re.I)
    ]
    for x in rows:
        x["thumbnail"] = x.pop("thumb", "")
        x["watchlisted"] = True
    return rows
