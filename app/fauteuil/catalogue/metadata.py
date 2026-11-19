"""Catalogue metadata and per-profile search history."""

import json
import time

from fauteuil.storage.database import connect, db

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
