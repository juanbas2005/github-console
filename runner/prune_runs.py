#!/usr/bin/env python3
"""Polla runs/ antiguos. Disparado por .github/workflows/cleanup-runs.yml."""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

SECONDS_PER_DAY = 86400


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", required=True)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    runs = Path(args.runs_dir)
    if not runs.is_dir():
        print(f"[prune] {runs} no existe, nada que podar")
        return 0

    cutoff = time.time() - args.days * SECONDS_PER_DAY
    removed = []
    for entry in sorted(runs.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        try:
            mtime = max(p.stat().st_mtime for p in entry.rglob("*"))
        except ValueError:
            continue
        if mtime < cutoff:
            subprocess.run(["git", "rm", "-r", "-q", "--", str(entry.relative_to(runs.parent))],
                           check=False, capture_output=True)
            removed.append(entry.name)

    if not removed:
        print(f"[prune] nada que podar (retención {args.days} días)")
        return 0

    git = ["git", "-C", str(runs.parent)]
    subprocess.run(git + ["commit", "-q", "-m",
                          f"console: prune {len(removed)} runs antiguos"],
                   check=True, capture_output=True)
    subprocess.run(git + ["push", "-q"], capture_output=True)
    print(f"[prune] eliminados: {', '.join(removed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
