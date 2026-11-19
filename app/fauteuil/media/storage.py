"""Measure media storage and clean unused generated covers."""

import errno
import hashlib
import os
import re
import shutil
import stat
import time

from fauteuil import config
from fauteuil.media.index import managed_downloads
from fauteuil.media.library import local_catalogue
from fauteuil.storage.database import db
from fauteuil.storage.files import directory_size, regular_file_size


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
