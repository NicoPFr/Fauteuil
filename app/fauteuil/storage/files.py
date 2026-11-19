"""Filesystem accounting without following symlinks."""

import os
import pathlib
import stat


def regular_file_size(path):
    try:
        info = path.stat(follow_symlinks=False)
        return info.st_size if stat.S_ISREG(info.st_mode) else 0
    except OSError:
        return 0


def directory_size(path):
    if path.is_symlink():
        return 0
    return sum(
        regular_file_size(pathlib.Path(root) / name)
        for root, _, files in os.walk(path, followlinks=False)
        for name in files
    )
