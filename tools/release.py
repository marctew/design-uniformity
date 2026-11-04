#!/usr/bin/env python3
"""Make a Design Uniformity release. Run from anywhere; it works on this folder's git repo.

  python tools/release.py 1.1            write version.json and MANIFEST.sha256, commit, tag v1.1, push
  python tools/release.py promote 1.1    point `stable` at v1.1 and push it: apps pick it up that night

Releasing doesn't reach any app until you promote. To test a release first, pin one app's
channel to "v1.1" in its /etc/design-uniformity/<app>.toml and use Check now there.
"""
import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP = {"MANIFEST.sha256"}


def git(*args, capture=False):
    p = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=capture)
    if p.returncode:
        sys.exit(f"git {' '.join(args)} failed{': ' + p.stderr.strip() if capture else ''}")
    return p.stdout if capture else ""


def tracked() -> list[str]:
    git("add", "-A")
    names = git("ls-files", "-z", capture=True).split("\0")
    return sorted(n for n in names if n and n not in SKIP)


def release(v: str):
    if not re.fullmatch(r"\d+\.\d+(\.\d+)?", v):
        sys.exit("Give the version as 1.1 or 1.1.2.")
    tag = "v" + v
    if git("tag", "-l", tag, capture=True).strip():
        sys.exit(f"{tag} already exists.")
    (ROOT / "version.json").write_text(json.dumps({"version": v, "date": date.today().isoformat()}, indent=2) + "\n")
    lines = []
    for name in tracked():
        data = subprocess.run(["git", "show", f":{name}"], cwd=ROOT, capture_output=True, check=True).stdout
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
    (ROOT / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", newline="\n")
    git("add", "-A")
    git("commit", "-q", "-m", f"Release {tag}")
    git("tag", "-a", tag, "-m", f"Design Uniformity {tag}")
    git("push", "-q", "origin", "HEAD", tag)
    print(f"Released {tag}. Nothing changes in the apps until: python tools/release.py promote {v}")


def promote(v: str):
    tag = "v" + v
    if not git("tag", "-l", tag, capture=True).strip():
        sys.exit(f"There's no {tag}.")
    git("tag", "-f", "-a", "stable", f"{tag}^{{}}", "-m", f"stable is {tag}")
    git("push", "-q", "-f", "origin", "refs/tags/stable")
    print(f"stable now points at {tag}. Apps update on their next nightly check, or with Check now.")


if __name__ == "__main__":
    if len(sys.argv) == 2:
        release(sys.argv[1])
    elif len(sys.argv) == 3 and sys.argv[1] == "promote":
        promote(sys.argv[2])
    else:
        sys.exit(__doc__)
