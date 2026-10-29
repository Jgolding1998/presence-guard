from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).absolute().parent.parent
PACKAGE = "opencv-contrib-python==4.13.0.92"


def plain(path):
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked installation paths are unsupported")


def install(root=ROOT, run=subprocess.run):
    root = Path(root).absolute()
    target = root / ".venv"
    plain(target)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "COMSPEC", "HOME", "LANG"}}
    common = {"cwd": root, "env": env, "check": True, "timeout": 300}
    if target.exists() and not (target / "pyvenv.cfg").is_file():
        raise ValueError("The .venv directory is not a recognized virtual environment")
    if not target.exists():
        run([sys.executable, "-I", "-m", "venv", str(target)], **common)
    plain(python)
    if not python.is_file():
        raise ValueError("The virtual environment is incomplete")
    run([str(python), "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "--require-virtualenv",
         "install", "--index-url", "https://pypi.org/simple", "--only-binary=:all:", "--no-cache-dir", PACKAGE], **common)
    run([str(python), "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "check"], **common)


def main():
    print("Optional camera support")
    print("This creates a local .venv and downloads the pinned OpenCV package and compatible dependencies from PyPI.")
    print("No global packages, administrator privileges, camera access, or desktop permissions are requested.")
    try:
        answer = input("Install camera support? Type yes to continue: ")
        if answer.strip().lower() != "yes":
            print("Nothing installed.")
            return 0
        install()
    except (OSError, ValueError, subprocess.SubprocessError, EOFError):
        print("Camera installation did not complete. Check Python, network access, and the local .venv. Each command has a five-minute timeout.", file=sys.stderr)
        return 1
    print("Camera support ready. Use .venv's Python to run: -m presence_guard --camera --open")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
