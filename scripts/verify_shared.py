from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).absolute().parent.parent
PAIRS = {"presence_guard/presence.py": "jarvis/presence.py", "presence_guard/actions.py": "jarvis/actions.py"}


def verify(root=ROOT, upstream=None):
    root = Path(root)
    try:
        manifest = json.loads((root / "shared-source.json").read_text("utf-8"))
        if manifest.get("schema") != 1 or len(manifest["files"]) != len(PAIRS):
            raise ValueError
        entries = {entry["vendored_path"]: entry for entry in manifest["files"]}
        if set(entries) != set(PAIRS):
            raise ValueError
        failures = []
        for vendored, source in PAIRS.items():
            entry = entries[vendored]
            digest = entry["sha256"]
            if entry["source_path"] != source or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError
            paths = [root / vendored]
            if upstream is not None:
                paths.append(Path(upstream) / source)
            for path in paths:
                if path.is_symlink() or path.stat().st_size > 100_000 or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    failures.append(vendored)
        return sorted(set(failures))
    except (OSError, ValueError, KeyError, TypeError):
        return ["Shared source manifest or files are invalid"]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify vendored guard rules and optionally compare a Jarvis developer checkout.")
    parser.add_argument("--upstream", type=Path, help="Local jarvis-workbench checkout root")
    args = parser.parse_args(argv)
    failures = verify(upstream=args.upstream)
    if failures:
        print("Shared source verification failed: " + ", ".join(failures), file=sys.stderr)
        return 1
    print("Shared source verified: 2 exact files" + (" and upstream checkout" if args.upstream else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
