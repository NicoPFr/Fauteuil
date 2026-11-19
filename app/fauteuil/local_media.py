"""Local media discovery, metadata, collections and cover generation."""

import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile
import threading
import time
import urllib.parse as up

from . import config
from .database import connect, db
from .files import regular_file_size
from .youtube_links import youtube_link

VIDEO_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}
LOCAL_VIDEO_SUFFIXES = VIDEO_CONTENT_TYPES.keys()


def video_metadata(path):
    path = pathlib.Path(path).resolve()
    info = {"duration": 0.0, "width": 0, "height": 0, "tags": {}}
    try:
        stat = path.stat()
    except OSError:
        return info
    rows = db(
        "select mtime,size,duration,width,height,tags from local_metadata where path=?",
        (str(path),),
    )
    if rows:
        cached = dict(rows[0])
        if (
            cached["mtime"] == stat.st_mtime
            and cached["size"] == stat.st_size
            and cached["tags"] is not None
        ):
            cached["tags"] = json.loads(cached["tags"])
            if "genre" in cached["tags"]:
                return cached
    try:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:format_tags=artist,description,synopsis,comment,genre:stream=codec_type,width,height:stream_disposition=attached_pic",
            "-of",
            "json",
            str(path),
        ]
        data = json.loads(
            subprocess.check_output(
                command, stderr=subprocess.DEVNULL, text=True, timeout=20
            )
        )
        container = data.get("format", {})
        info["duration"] = float(container.get("duration") or 0)
        info["tags"] = container.get("tags", {})
        info["tags"].setdefault("genre", "")
        video = next(
            (
                stream
                for stream in data.get("streams", [])
                if stream.get("codec_type") == "video"
                and not stream.get("disposition", {}).get("attached_pic")
            ),
            {},
        )
        info["width"] = int(video.get("width") or 0)
        info["height"] = int(video.get("height") or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return info
    db(
        """insert or replace into local_metadata(path,mtime,size,duration,width,height,tags)
       values(?,?,?,?,?,?,?)""",
        (
            str(path),
            stat.st_mtime,
            stat.st_size,
            info["duration"],
            info["width"],
            info["height"],
            json.dumps(info["tags"]),
        ),
        write=True,
    )
    return info


def embedded_youtube_id(tags):
    return youtube_link(tags.get("comment", ""))[0]


def local_youtube_file(video_id):
    for path, _, _ in local_catalogue():
        if embedded_youtube_id(video_metadata(path)["tags"]) == video_id:
            return path
    return None


def natural_key(value):
    import unicodedata

    return [
        int(z) if z.isdigit() else z.lower()
        for z in re.split(r"(\d+)", unicodedata.normalize("NFKD", str(value)))
    ]


def is_local_video(path):
    if path.suffix.lower() not in LOCAL_VIDEO_SUFFIXES or not path.is_file():
        return False
    return config.MOVIES.resolve() in path.parents or bool(
        db("select 1 from imported_videos where path=?", (str(path),))
    )


def register_local_videos(paths):
    library = config.MOVIES.resolve()
    protected = config.DATA.resolve()
    added = 0
    skipped = 0
    with connect() as connection:
        for value in paths:
            try:
                if not isinstance(value, str) or not pathlib.Path(value).is_absolute():
                    raise ValueError
                path = pathlib.Path(value).resolve(strict=True)
                if (
                    protected in path.parents
                    or library in path.parents
                    or not path.is_file()
                    or path.suffix.lower() not in LOCAL_VIDEO_SUFFIXES
                ):
                    skipped += 1
                    continue
                with path.open("rb"):
                    pass
            except (OSError, ValueError, RuntimeError):
                skipped += 1
                continue
            result = connection.execute(
                "insert or ignore into imported_videos values(?,?)",
                (str(path), time.time()),
            )
            added += result.rowcount
            skipped += 1 - result.rowcount
    return {"ok": True, "added": added, "skipped": skipped}


def imported_entries():
    for row in db("select path from imported_videos order by added_at desc"):
        path = pathlib.Path(row["path"])
        try:
            available = path.resolve(strict=True) == path and path.is_file()
        except (OSError, RuntimeError):
            available = False
        yield path, available


def episode_number(path, *, allow_bare_number=False):
    stem = path.stem.replace("_", " ").strip()
    match = re.search(r"\b(?:[ÉE]pisode\s*|S\d{1,2}[ .-]*E)(\d{1,3})\b", stem, re.I)
    if not match and allow_bare_number:
        match = re.match(r"^\s*(?:Ep?\s*)?(\d{1,3})(?=\s*[-. ]|$)", stem, re.I)
    return int(match.group(1)) if match else None


def episode_order(entry):
    path, season = entry
    episode = episode_number(path, allow_bare_number=True)
    return (
        season or 1,
        episode if episode is not None else float("inf"),
        natural_key(path.stem),
    )


def imported_series(path):
    folder_season = re.fullmatch(
        r"(?:season|saison|s)[ ._-]*(\d+)", path.parent.name, re.I
    )
    filename_season = re.search(
        r"\bS(\d{1,2})[ .-]*E\d+", path.stem.replace("_", " "), re.I
    )
    series_root = path.parent.parent if folder_season else path.parent
    home = pathlib.Path.home().resolve()
    standard_folders = {
        home,
        home / "Desktop",
        home / "Downloads",
        home / "Documents",
        home / "Movies",
    }
    if series_root in standard_folders or series_root == series_root.parent:
        return None
    if not (folder_season or episode_number(path) is not None):
        return None
    season = folder_season or filename_season
    return series_root, int(season.group(1)) if season else 1


def local_catalogue():
    library = config.MOVIES.resolve()
    folders = {}
    films = []
    for path in sorted(library.rglob("*"), key=lambda p: natural_key(p.as_posix())):
        if (
            path.suffix.lower() not in LOCAL_VIDEO_SUFFIXES
            or not path.is_file()
            or path.is_symlink()
        ):
            continue
        if path.parent == library:
            films.append((path, None, None))
            continue
        folder_season = re.fullmatch(
            r"(?:season|saison|s)[ ._-]*(\d+)", path.parent.name, re.I
        )
        if path.parent.parent == library:
            folder_season = None
        filename_season = re.search(
            r"\bS(\d{1,2})[ .-]*E\d+", path.stem.replace("_", " "), re.I
        )
        season = folder_season or filename_season
        series_root = path.parent.parent if folder_season else path.parent
        folders.setdefault(series_root, []).append(
            (path, int(season.group(1)) if season else None)
        )
    for path, available in imported_entries():
        if not available or library in path.parents:
            continue
        group = imported_series(path)
        if group:
            root, season = group
            folders.setdefault(root, []).append((path, season))
        else:
            films.append((path, None, None))
    for root, entries in folders.items():
        if len(entries) == 1 and entries[0][1] is None:
            films.append((entries[0][0], None, None))
            continue
        for path, season in sorted(entries, key=episode_order):
            films.append((path, root, season or 1))
    return films


def series_identifier(root):
    library = config.MOVIES.resolve()
    if root.is_relative_to(library):
        return str(root.relative_to(library))
    return "imported:" + hashlib.sha256(os.fsencode(root)).hexdigest()


def display_title(path):
    path = pathlib.Path(path)
    stem = path.stem.replace("_", " ").strip()
    episode = episode_number(path, allow_bare_number=True)
    if episode is not None:
        return "Épisode " + str(episode).zfill(2)
    stem = re.sub(
        r"\s*[\[(](?:[^\])]*(?:sub|subtitle|1080p|720p|4k|fr|en)[^\])]*)[\])]\s*$",
        "",
        stem,
        flags=re.I,
    )
    stem = re.sub(r"^\s*\d{1,3}\s*[-_.]\s*", "", stem).strip(" -_.")
    return stem or pathlib.Path(path).stem


cover_generation = threading.Lock()


def local_cover_url(path):
    return "/api/thumb?" + up.urlencode({"video": str(path)})


def local_cover(path):
    for suffix in (".jpg", ".png", ".webp", ".jpeg"):
        sidecar = path.with_suffix(suffix)
        if sidecar.is_file() and not sidecar.is_symlink():
            return sidecar
    cache = config.DATA / "jaquettes"
    if cache.is_symlink():
        return None
    cover = cache / (hashlib.sha1(str(path).encode()).hexdigest() + ".jpg")
    if regular_file_size(cover):
        return cover
    with cover_generation:
        if regular_file_size(cover):
            return cover
        try:
            if (
                cover.is_file()
                and not cover.is_symlink()
                and time.time() - cover.stat().st_mtime < 300
            ):
                return None
            duration = video_metadata(path)["duration"]
            at = max(0, min(duration * 0.35, duration - 0.1))
            cache.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(dir=cache) as work:
                image = pathlib.Path(work) / "frame.jpg"
                try:
                    result = subprocess.run(
                        [
                            "ffmpeg",
                            "-y",
                            "-ss",
                            str(at),
                            "-i",
                            str(path),
                            "-map",
                            "0:V:0",
                            "-frames:v",
                            "1",
                            "-vf",
                            "scale=480:-2",
                            str(image),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=8,
                    )
                    success = result.returncode == 0 and bool(regular_file_size(image))
                except (OSError, subprocess.TimeoutExpired):
                    success = False
                if not success:
                    image.write_bytes(b"")
                image.replace(cover)
                return cover if success else None
        except OSError:
            return None
