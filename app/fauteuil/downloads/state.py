"""Volatile job registry, owned by the download worker process."""

from _thread import LockType
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock


@dataclass
class JobRegistry:
    active: dict[str, Path | None] = field(default_factory=dict)
    process_groups: dict[int, Event | None] = field(default_factory=dict)
    playback: dict[str, dict] = field(default_factory=dict)
    lock: LockType = field(default_factory=Lock)
    closing: Event = field(default_factory=Event)


registry = JobRegistry()
