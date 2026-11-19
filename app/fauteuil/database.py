"""SQLite connections and backwards-compatible schema migrations."""

import sqlite3
from contextlib import contextmanager

from . import config


@contextmanager
def connect():
    connection = sqlite3.connect(config.DB)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize():
    config.DATA.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.execute("begin immediate")
        c.execute(
            "create table if not exists videos(id text primary key,title text,thumb text,duration integer,channel text)"
        )
        if "genre" not in {row[1] for row in c.execute("pragma table_info(videos)")}:
            c.execute("alter table videos add column genre text not null default ''")
        c.execute(
            "create table if not exists video_synopses(id text primary key, description text, fetched_at real)"
        )
        if "youtube_state" not in {
            row[1] for row in c.execute("pragma table_info(video_synopses)")
        }:
            c.execute(
                "alter table video_synopses add column youtube_state text not null default ''"
            )
        c.execute(
            "create table if not exists watchlist(id text primary key,title text,thumb text,duration integer,channel text,added real)"
        )
        c.execute(
            "create table if not exists playback(id text primary key,path text,current real,duration real,updated real)"
        )
        c.execute(
            "create table if not exists settings(key text primary key,value text)"
        )
        c.execute(
            "create table if not exists profiles(id text primary key,name text,avatar text)"
        )
        c.execute(
            "create table if not exists watchlist_profile(profile text,id text,title text,thumb text,duration integer,channel text,added real,primary key(profile,id))"
        )
        c.execute(
            "create table if not exists playback_profile(profile text,id text,path text,current real,duration real,updated real,primary key(profile,id))"
        )
        c.execute(
            "create table if not exists completed_playback(profile text,id text,finished_at real,primary key(profile,id))"
        )
        c.execute(
            "create table if not exists temporary_downloads(id text primary key,path text,created real)"
        )
        c.execute(
            "create table if not exists pending_playback_cleanup(id text,path text,primary key(id,path))"
        )
        if "kept" not in {
            row[1] for row in c.execute("pragma table_info(temporary_downloads)")
        }:
            c.execute(
                "alter table temporary_downloads add column kept integer not null default 0"
            )
        c.execute(
            "create table if not exists imported_videos(path text primary key, added_at real)"
        )
        c.execute(
            "create table if not exists local_metadata(path text primary key,mtime real,size integer,duration real,width integer,height integer)"
        )
        c.execute(
            "create table if not exists recent_searches(profile text, term_key text, term text, video_ids text, searched_at real, primary key(profile,term_key))"
        )
        if "tags" not in {
            row[1] for row in c.execute("pragma table_info(local_metadata)")
        }:
            c.execute("alter table local_metadata add column tags text")
        c.execute("insert or ignore into profiles values('default','Principal','🍿')")
        if not c.execute(
            "select 1 from settings where key='profile_migration_done'"
        ).fetchone():
            c.execute(
                "insert or ignore into watchlist_profile select 'default',id,title,thumb,duration,channel,added from watchlist"
            )
            c.execute(
                "insert or ignore into playback_profile select 'default',id,path,current,duration,updated from playback"
            )
            c.execute("insert into settings values('profile_migration_done','1')")
        c.execute(
            "create table if not exists youtube_playlists(profile text,id text,title text,description text,thumbnail text,channel text,total integer,loaded_at real not null default 0,synced_at real,primary key(profile,id))"
        )
        c.execute(
            "create table if not exists youtube_playlist_items(profile text,playlist text,position integer,video text,primary key(profile,playlist,position))"
        )


def active_profile():
    rows = db("select value from settings where key='active_profile'")
    return rows[0]["value"] if rows else "default"


def db(query, args=(), write=False):
    with connect() as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(query, args)
        return cursor.fetchall() if not write else None
