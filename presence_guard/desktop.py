from __future__ import annotations

import secrets
import stat
import sys
from pathlib import Path

from .actions import Actions


def plain_path(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError("Linked paths are unavailable")
    return path


class DesktopActions(Actions):
    def __init__(self, root, **settings):
        self.runtime = Path(root) / "runtime"
        self.session_dir = None
        self.alerts = 0
        super().__init__(output_dir=self.runtime, **settings)

    def run(self, action):
        if action == "boss_alert" and not self.demo and self.allow_sheet and sys.platform == "win32":
            plain_path(self.runtime)
            self.runtime.mkdir(exist_ok=True)
            if self.session_dir is None:
                candidate = self.runtime / ("session-" + secrets.token_hex(12))
                candidate.mkdir(mode=0o700)
                self.session_dir = candidate
            plain_path(self.session_dir)
            self.alerts += 1
            folder = self.session_dir / ("alert-" + str(self.alerts))
            folder.mkdir(mode=0o700)
            plain_path(folder)
            self.output_dir = folder
        return super().run(action)
