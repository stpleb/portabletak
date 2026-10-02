#!/usr/bin/env python3
"""Remove installed dependency test suites from a marked clean payload.

Wheel test fixtures can contain sample private keys and certificates. They are
not runtime software and must not ship in the generic PortableTAK base.
"""
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1]).absolute()
marker = root.parent / ".portabletak-development"
if root.resolve() != root or root.name != "payload" or not marker.is_file():
    raise SystemExit("marked temporary clean payload required")

site = root / "opentak/venv/lib/python3.12/site-packages"
if not site.is_dir() or site.is_symlink():
    raise SystemExit("clean Python site-packages missing")

targets = sorted(
    (path for path in site.rglob("*") if path.is_dir() and not path.is_symlink()
     and path.name.lower() in {"test", "tests"}),
    key=lambda path: len(path.parts),
    reverse=True,
)
removed = 0
for path in targets:
    if path.exists():
        shutil.rmtree(path)
        removed += 1

print(f"Removed {removed} dependency test directories")
