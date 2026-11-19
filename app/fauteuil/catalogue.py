"""Cached video metadata, search history and storage reporting."""

import errno
import hashlib
import json
import os
import re
import shutil
import stat
import time

from . import config
from .database import connect, db
from .files import directory_size, regular_file_size
from .local_media import local_catalogue
from .media_index import managed_downloads

GENRE_LABELS = {
    "film & animation": "Cinéma et animation",
    "entertainment": "Divertissement",
    "music": "Musique",
    "sports": "Sport",
    "gaming": "Jeux vidéo",
    "education": "Éducation",
    "science & technology": "Science et technologie",
    "people & blogs": "Vlogs",
}


def genre_label(value):
    if not isinstance(value, str):
        return ""
    value = value.strip()[:80]
    return GENRE_LABELS.get(value.casefold(), value)


def add(items):
    with connect() as connection:
        connection.executemany(
            """insert into videos(id,title,thumb,duration,channel,genre) values(?,?,?,?,?,?)
      on conflict(id) do update set title=excluded.title, thumb=excluded.thumb,
      duration=excluded.duration, channel=excluded.channel,
      genre=coalesce(nullif(excluded.genre,''),videos.genre)""",
            [
                (
                    item["id"],
                    item.get("title", "Vidéo"),
                    item.get("thumbnail", ""),
                    item.get("duration") or 0,
                    item.get("channel", "YouTube"),
                    genre_label(item.get("genre")),
                )
                for item in items
            ],
        )


def remember_search(profile, term, items):
    ids = list(dict.fromkeys(video["id"] for video in items))
    if not ids:
        return
    with connect() as connection:
        if not connection.execute(
            "select 1 from profiles where id=?", (profile,)
        ).fetchone():
            return
        connection.execute(
            "insert or replace into recent_searches values(?,?,?,?,?)",
            (profile, term.casefold(), term, json.dumps(ids), time.time()),
        )
        connection.execute(
            """delete from recent_searches where profile=? and term_key not in
   (select term_key from recent_searches where profile=? order by searched_at desc limit 6)""",
            (profile, profile),
        )


def recent_searches(profile):
    rows = db(
        "select term,video_ids from recent_searches where profile=? order by searched_at desc",
        (profile,),
    )
    return [{"term": row["term"], "ids": json.loads(row["video_ids"])} for row in rows]


def unused_cover_cache(remove=False, catalogue=None):
    if catalogue is None:
        catalogue = local_catalogue()
    paths = {str(path) for path, _, _ in catalogue}
    paths.update(row["path"] for row in db("select path from imported_videos"))
    current = {hashlib.sha1(path.encode()).hexdigest() + ".jpg" for path in paths}
    cache = config.DATA / "jaquettes"
    try:
        directory = os.open(cache, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        if error.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
            return {"count": 0, "bytes": 0}
        raise
    try:
        older_than = time.time() - 600
        count = size = 0
        for name in os.listdir(directory):
            # Older builds named generated covers with Python's decimal hash.
            if name in current or not re.fullmatch(
                r"(?:[a-f0-9]{40}|[0-9]{1,19})\.jpg", name
            ):
                continue
            try:
                entry = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(entry.st_mode) or entry.st_mtime > older_than:
                    continue
                if remove:
                    os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                continue
            count += 1
            size += entry.st_size
        return {"count": count, "bytes": size}
    finally:
        os.close(directory)


def storage_usage():
    volume = shutil.disk_usage(config.DATA)
    catalogue = local_catalogue()
    temp = config.DATA / "À regarder"
    covers = config.DATA / "jaquettes"
    database_bytes = sum(
        regular_file_size(config.DB.with_name(config.DB.name + suffix))
        for suffix in ("", "-wal", "-shm", "-journal")
    )
    other_cache_bytes = directory_size(config.DATA / "cache")
    retained_bytes = sum(
        regular_file_size(entry["path"])
        for entry in managed_downloads()
        if entry["kept"]
    )
    return {
        "available_bytes": volume.free,
        "volume_bytes": volume.total,
        "personal_video_bytes": sum(
            regular_file_size(path) for path, _, _ in catalogue
        ),
        "temporary_bytes": max(0, directory_size(temp) - retained_bytes),
        "retained_video_bytes": retained_bytes,
        "cover_bytes": directory_size(covers),
        "unused_covers": unused_cover_cache(catalogue=catalogue),
        "other_cache_bytes": other_cache_bytes,
        "database_bytes": database_bytes,
    }
