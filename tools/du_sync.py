#!/usr/bin/env python3
"""Design Uniformity sync: keeps one app's copy of the design files up to date from GitHub.

Runs on the app's host (not inside a container), as root, from systemd:
  du_sync.py run /etc/design-uniformity/<app>.toml       check, and update if a new release is out
  du_sync.py rollback /etc/design-uniformity/<app>.toml  put the previous version back
  du_sync.py status /etc/design-uniformity/<app>.toml    print the status file
  du_sync.py install /etc/design-uniformity/<app>.toml   write and start the systemd timer and path units

The app never runs this itself. Its Settings page (du_flask.py) reads <state_dir>/status.json and
asks for a check or a rollback by creating <state_dir>/check-now or <state_dir>/rollback, which a
systemd path unit turns into a run. That works the same for systemd apps and Docker apps.

An update: download the release tarball, check every file against MANIFEST.sha256, refuse if the
app's repo has uncommitted changes, back up the current copies, copy the new ones in, commit and
push them (so the app's own `git pull` never fights the change), run the app's deploy command,
then wait for its health URL. If the app doesn't come back healthy, the previous copies go back
and it's deployed again. Needs Python 3.11+ (tomllib); 3.12+ for the safe tar filter.
"""
from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import pwd
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = "marctew/design-uniformity"
DEFAULT_SOURCE = "https://codeload.github.com/" + REPO + "/tar.gz/refs/tags/{ref}"
INSTALL_DIR = Path("/opt/design-uniformity")
UNIT_DIR = Path("/etc/systemd/system")
COMMIT_EMAIL = "marctew@users.noreply.github.com"
HEALTH_WAIT = 90          # seconds to wait for the app after a deploy
USER_AGENT = "design-uniformity-sync"


# ---------------------------------------------------------------- small helpers
def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Stop(Exception):
    """Ends a run with a result for the status file: blocked, failed or rolled_back."""
    def __init__(self, result: str, message: str):
        super().__init__(message)
        self.result, self.message = result, message


def load_config(path: str) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    for key in ("app", "repo", "state_dir", "files"):
        if key not in cfg:
            raise SystemExit(f"{path}: '{key}' is missing.")
    cfg.setdefault("channel", "stable")
    cfg.setdefault("auto", True)
    cfg.setdefault("git", (Path(cfg["repo"]) / ".git").exists())
    cfg.setdefault("push", cfg["git"])
    cfg.setdefault("require_clean", cfg["git"])
    cfg.setdefault("deploy", "")
    cfg.setdefault("health", "")
    cfg.setdefault("source", DEFAULT_SOURCE)
    cfg["id"] = Path(path).stem
    cfg["config_path"] = str(Path(path).resolve())
    return cfg


class State:
    """status.json and prefs.json in the app's state folder. The app reads status and writes prefs."""
    def __init__(self, cfg: dict):
        self.dir = Path(cfg["state_dir"])
        self.dir.mkdir(parents=True, exist_ok=True)
        self.owner = cfg.get("state_owner")       # Docker apps: the container's user writes the flags
        if self.owner:
            pw = pwd.getpwnam(self.owner)
            os.chown(self.dir, pw.pw_uid, pw.pw_gid)
        self.path = self.dir / "status.json"
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}
        self.data.update({"app": cfg["app"], "channel": cfg["channel"]})

    def prefs(self) -> dict:
        try:
            return json.loads((self.dir / "prefs.json").read_text())
        except (OSError, ValueError):
            return {}

    def save(self, **changes):
        self.data.update(changes)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.path)


def run(cmd, cwd=None, user=None, check=True, timeout=600) -> subprocess.CompletedProcess:
    """Run a command, as `user` if given (git runs as the repo's owner, so files never end up root-owned)."""
    if isinstance(cmd, str):
        cmd = ["/bin/sh", "-c", cmd]
    if user and user != pwd.getpwuid(os.getuid()).pw_name:
        cmd = ["runuser", "-u", user, "--"] + cmd
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        tail = (p.stderr or p.stdout).strip().splitlines()[-3:]
        raise RuntimeError(f"{' '.join(cmd[-4:])} failed: {' '.join(tail) or 'exit ' + str(p.returncode)}")
    return p


# ---------------------------------------------------------------- the release
def fetch(cfg: dict, ref: str) -> bytes:
    url = cfg["source"].format(ref=ref)
    if url.startswith("file://"):
        return Path(url[7:]).read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise Stop("failed", f"GitHub has no release called '{ref}'. Check the channel in {cfg['config_path']}.")
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        time.sleep(5 * (attempt + 1))
    raise Stop("failed", f"Can't reach GitHub: {last}. It tries again tonight.")


def unpack(blob: bytes, into: Path) -> tuple[Path, dict]:
    """Unpack the tarball and check it. Returns the release root and its version.json."""
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(into, filter="data")
        else:                                         # Python 3.11: check paths by hand
            for m in tar.getmembers():
                if m.name.startswith("/") or ".." in Path(m.name).parts or not (m.isfile() or m.isdir()):
                    raise Stop("failed", f"The release has an unsafe entry ({m.name}), so nothing was changed.")
            tar.extractall(into)
    roots = [p for p in into.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise Stop("failed", "The release tarball isn't laid out as expected, so nothing was changed.")
    root = roots[0]
    try:
        version = json.loads((root / "version.json").read_text())
        manifest = (root / "MANIFEST.sha256").read_text().splitlines()
    except (OSError, ValueError):
        raise Stop("failed", "The release has no version.json or MANIFEST.sha256, so nothing was changed.")
    sums = {}
    for line in manifest:
        if line.strip():
            digest, name = line.split(None, 1)
            sums[name.strip()] = digest
    for name, digest in sums.items():
        f = root / name
        if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != digest:
            raise Stop("failed", f"{name} doesn't match the release manifest, so nothing was changed.")
    version["_manifest"] = sums
    return root, version


def plan(cfg: dict, root: Path, manifest: dict) -> list[tuple[Path, Path]]:
    """(source, destination) pairs from [files]. A key ending in / copies a whole folder."""
    repo, pairs = Path(cfg["repo"]), []
    for src, dst in cfg["files"].items():
        if src.endswith("/"):
            names = [n for n in manifest if n.startswith(src)]
            if not names:
                raise Stop("failed", f"The release has nothing under {src}. Check [files] in {cfg['config_path']}.")
            pairs += [(root / n, repo / dst / n[len(src):]) for n in names]
        else:
            if src not in manifest:
                raise Stop("failed", f"The release has no {src}. Check [files] in {cfg['config_path']}.")
            pairs.append((root / src, repo / dst))
    for _, d in pairs:
        if repo.resolve() not in d.resolve().parents:
            raise Stop("failed", f"{d} is outside {repo}. Check [files] in {cfg['config_path']}.")
    return pairs


# ---------------------------------------------------------------- applying
def owner_of(path: str) -> str:
    return pwd.getpwuid(os.stat(path).st_uid).pw_name


def git(cfg, *args, check=True):
    return run(["git", *args], cwd=cfg["repo"], user=owner_of(cfg["repo"]), check=check)


def dirty(cfg) -> list[str]:
    out = git(cfg, "status", "--porcelain", "--untracked-files=no").stdout
    return [line[3:] for line in out.splitlines() if line.strip()]


def copy_in(pairs, owner: str, backup: Path | None):
    """Copy files into the repo, keeping the current ones in `backup` first."""
    pw = pwd.getpwnam(owner)
    for src, dst in pairs:
        if backup is not None and dst.exists():
            keep = backup / dst.relative_to(dst.anchor)
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, keep)
        made = []
        p = dst.parent
        while not p.exists():
            made.append(p)
            p = p.parent
        dst.parent.mkdir(parents=True, exist_ok=True)
        for d in made:
            os.chown(d, pw.pw_uid, pw.pw_gid)
        tmp = dst.with_name(dst.name + ".du-tmp")
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o644)
        os.chown(tmp, pw.pw_uid, pw.pw_gid)
        os.replace(tmp, dst)


def restore(pairs, backup: Path):
    for _, dst in pairs:
        keep = backup / dst.relative_to(dst.anchor)
        if keep.exists():
            shutil.copy2(keep, dst)
        elif dst.exists():
            dst.unlink()                 # it was new in this version


def commit(cfg, pairs, message: str) -> bool:
    paths = [str(d.relative_to(cfg["repo"])) for _, d in pairs]
    git(cfg, "add", "--", *paths)
    if not git(cfg, "diff", "--cached", "--quiet", check=False).returncode:
        return False                     # nothing actually changed
    git(cfg, "-c", "user.name=Design Uniformity", "-c", f"user.email={COMMIT_EMAIL}",
        "commit", "--quiet", "-m", message, "--", *paths)
    return True


def push(cfg) -> str:
    """Push if there's anything to push. Returns a warning, or ''."""
    if not cfg["push"]:
        return ""
    p = git(cfg, "push", "--quiet", check=False)
    if p.returncode:
        return "Couldn't push to GitHub, so the commit is only on this host for now. It tries again on the next run."
    return ""


def deploy_and_check(cfg) -> str:
    """Run the deploy command and wait for the health URL. Returns '' when healthy, or why not."""
    if cfg["deploy"]:
        p = run(cfg["deploy"], cwd=cfg["repo"], check=False, timeout=1800)
        if p.returncode:
            tail = " ".join((p.stderr or p.stdout).strip().splitlines()[-2:])
            return f"the deploy command failed ({tail or 'exit ' + str(p.returncode)})"
    if not cfg["health"]:
        return ""
    deadline, last = time.time() + HEALTH_WAIT, "no answer"
    time.sleep(2)
    while time.time() < deadline:
        try:
            req = urllib.request.Request(cfg["health"], headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=5) as r:
                if r.status < 500:
                    return ""
                last = f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            if e.code < 500:             # a login page or 401 still means it's up
                return ""
            last = f"HTTP {e.code}"
        except Exception as e:
            last = str(e)
        time.sleep(3)
    return f"it didn't answer at {cfg['health']} within {HEALTH_WAIT}s ({last})"


# ---------------------------------------------------------------- commands
def cmd_run(cfg: dict, state: State, force_apply: bool):
    prefs = state.prefs()
    auto = prefs.get("auto", cfg["auto"])
    ref = cfg["channel"]
    state.save(busy=True, auto=auto)
    with tempfile.TemporaryDirectory(prefix="du-") as tmp:
        root, version = unpack(fetch(cfg, ref), Path(tmp))
        tag = "v" + str(version["version"])
        state.save(available=tag, available_date=version.get("date"), checked_at=now())
        if pending_push(cfg):
            warn = push(cfg)
            if warn:
                state.save(warning=warn)
            else:
                state.data.pop("warning", None)
        if state.data.get("installed") == tag and installed_matches(cfg, root, version):
            state.save(result="ok", message="Up to date.")
            return
        if not (auto or force_apply):
            state.save(result="available", message=f"{tag} is out. Automatic updates are off, so use Check now to install it.")
            return
        apply(cfg, state, root, version, tag)
        self_update(root)


def installed_matches(cfg, root, version) -> bool:
    try:
        pairs = plan(cfg, root, version["_manifest"])
    except Stop:
        return False
    return all(d.exists() and d.read_bytes() == s.read_bytes() for s, d in pairs)


def pending_push(cfg) -> bool:
    if not (cfg["git"] and cfg["push"]):
        return False
    p = git(cfg, "rev-list", "--count", "@{u}..HEAD", check=False)
    return p.returncode == 0 and p.stdout.strip() not in ("", "0")


def apply(cfg, state: State, root: Path, version: dict, tag: str, verb="Update"):
    pairs = plan(cfg, root, version["_manifest"])
    owner = owner_of(cfg["repo"])
    if cfg["git"] and cfg["require_clean"]:
        changed = dirty(cfg)
        if changed:
            shown = ", ".join(changed[:4]) + (f" and {len(changed) - 4} more" if len(changed) > 4 else "")
            raise Stop("blocked", f"{tag} is out, but {cfg['repo']} has uncommitted changes ({shown}). "
                                  "Commit or discard them and it updates on the next run.")
    old = state.data.get("installed")
    backup = state.dir / "prev"
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir()
    copy_in(pairs, owner, backup)
    committed = False
    if cfg["git"]:
        committed = commit(cfg, pairs, f"{verb} Design Uniformity to {tag}")
    warn = push(cfg) if committed else ""
    problem = deploy_and_check(cfg)
    if problem:
        restore(pairs, backup)
        if committed:
            commit(cfg, pairs, f"Put Design Uniformity back to {old or 'the previous copy'} ({tag} failed to deploy)")
            push(cfg)
        again = deploy_and_check(cfg)
        msg = f"{tag} was put back to {old or 'the previous copy'} because {problem}."
        if again:
            msg += f" The app is still unhealthy after that: {again}."
        raise Stop("rolled_back", msg)
    if old and old != tag:
        state.save(previous=old)                  # a rollback fetches that tag from GitHub again
    state.save(installed=tag, installed_date=version.get("date"), installed_at=now(),
               result="updated", message=f"Updated to {tag}." if verb == "Update" else f"Rolled back to {tag}.",
               **({"warning": warn} if warn else {}))
    if not warn:
        state.data.pop("warning", None)
        state.save()


def cmd_rollback(cfg: dict, state: State):
    prev = state.data.get("previous")
    if not prev:
        raise Stop("failed", "There's no earlier version to go back to yet.")
    state.save(busy=True)
    with tempfile.TemporaryDirectory(prefix="du-") as tmp:
        root, version = unpack(fetch(cfg, prev), Path(tmp))
        current = state.data.get("installed")
        apply(cfg, state, root, version, prev, verb="Roll back")
        state.save(previous=current, message=f"Rolled back to {prev}. Automatic updates are now off, "
                   "so it stays on this version until you use Check now.", result="updated")
        prefs = state.dir / "prefs.json"
        prefs.write_text(json.dumps({**state.prefs(), "auto": False}))
        if state.owner:
            pw = pwd.getpwnam(state.owner)
            os.chown(prefs, pw.pw_uid, pw.pw_gid)
        state.save(auto=False)


def self_update(root: Path):
    """Replace this script with the release's copy, so the updater improves along with everything else."""
    new = root / "tools" / "du_sync.py"
    mine = INSTALL_DIR / "du_sync.py"
    if new.exists() and mine.exists() and new.read_bytes() != mine.read_bytes():
        tmp = mine.with_suffix(".new")
        shutil.copyfile(new, tmp)
        os.chmod(tmp, 0o755)
        os.replace(tmp, mine)


def cmd_install(cfg: dict):
    if Path(__file__).resolve() != (INSTALL_DIR / "du_sync.py").resolve():
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(__file__, INSTALL_DIR / "du_sync.py")
        os.chmod(INSTALL_DIR / "du_sync.py", 0o755)
    state_dir = Path(cfg["state_dir"])
    state_dir.mkdir(parents=True, exist_ok=True)
    name = f"du-sync-{cfg['id']}"
    units = {
        f"{name}.service": f"""[Unit]
Description=Design Uniformity sync for {cfg['app']}
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {INSTALL_DIR}/du_sync.py flagged {cfg['config_path']}
""",
        f"{name}.timer": f"""[Unit]
Description=Nightly Design Uniformity check for {cfg['app']}

[Timer]
OnCalendar=*-*-* 02:30
RandomizedDelaySec=2h
Persistent=true

[Install]
WantedBy=timers.target
""",
        f"{name}.path": f"""[Unit]
Description=Design Uniformity Check now / Roll back requests for {cfg['app']}

[Path]
PathExists={state_dir}/check-now
PathExists={state_dir}/rollback
Unit={name}.service

[Install]
WantedBy=paths.target
""",
    }
    for fname, text in units.items():
        (UNIT_DIR / fname).write_text(text)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", f"{name}.timer", f"{name}.path"])
    print(f"Installed {name}.timer (nightly) and {name}.path (Check now). Run it once now with:\n"
          f"  systemctl start {name}.service")


def main(argv):
    if len(argv) != 3 or argv[1] not in ("run", "flagged", "rollback", "status", "install"):
        print(__doc__)
        return 2
    cfg = load_config(argv[2])
    if argv[1] == "install":
        cmd_install(cfg)
        return 0
    state = State(cfg)
    if argv[1] == "status":
        print(json.dumps(state.data, indent=2))
        return 0
    lock = open(state.dir / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another run is going.")
        return 0
    action = argv[1]
    force = False
    if action == "flagged":                       # from systemd: the timer, or a flag from the app
        if (state.dir / "rollback").exists():
            action = "rollback"
        elif (state.dir / "check-now").exists():
            action, force = "run", True
        else:
            action = "run"
    for flag in ("check-now", "rollback"):
        (state.dir / flag).unlink(missing_ok=True)
    try:
        if action == "rollback":
            cmd_rollback(cfg, state)
        else:
            cmd_run(cfg, state, force_apply=force or argv[1] == "run")
    except Stop as e:
        state.save(result=e.result, message=e.message, checked_at=now())
        print(e.message, file=sys.stderr)
        return 1
    except Exception as e:                        # anything unexpected still ends up on the Settings card
        state.save(result="failed", message=f"The update stopped: {e}", checked_at=now())
        raise
    finally:
        state.save(busy=False)
    print(state.data.get("message", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
