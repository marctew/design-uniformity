#!/usr/bin/env python3
"""Checks for du_sync.py and du_flask.py against fake releases and a throwaway git repo. Linux, as root.

  sudo python3 tools/check_du_sync.py

Builds releases v1.0 and v1.1 from this folder into a temp dir, served as file:// URLs, so nothing
touches GitHub or a real app. The "app" is a git repo owned by a normal user with a bare remote,
and its health URL is a tiny HTTP server that can be told to fail.
"""
import hashlib
import http.server
import io
import json
import os
import pwd
import subprocess
import sys
import tarfile
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import du_sync  # noqa: E402

OWNER = os.environ.get("DU_TEST_USER") or next(
    p.pw_name for p in pwd.getpwall() if 1000 <= p.pw_uid < 60000)
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def make_release(out: Path, version: str, tweak: str = ""):
    files = {}
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT).as_posix()
        if p.is_file() and not rel.startswith((".git/", "__pycache__")) and "/__pycache__/" not in rel \
                and rel not in ("MANIFEST.sha256", "version.json"):
            files[rel] = p.read_bytes()
    files["css/uniform.css"] += tweak.encode()
    files["version.json"] = json.dumps({"version": version, "date": "2026-11-04"}).encode()
    files["MANIFEST.sha256"] = "".join(
        f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in sorted(files.items())).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for n, d in files.items():
            ti = tarfile.TarInfo(f"design-uniformity-{version}/{n}")
            ti.size = len(d)
            tar.addfile(ti, io.BytesIO(d))
    (out / f"v{version}.tar.gz").write_bytes(buf.getvalue())


class Health(http.server.BaseHTTPRequestHandler):
    status = 200

    def do_GET(self):
        self.send_response(Health.status)
        self.end_headers()

    def log_message(self, *a):
        pass


def sh(cmd, cwd=None, user=None):
    return du_sync.run(cmd, cwd=cwd, user=user)


def main():
    if os.geteuid():
        sys.exit("Run as root (it creates files as another user, like the real thing).")
    tmp = Path(tempfile.mkdtemp(prefix="du-check-"))
    os.chmod(tmp, 0o755)
    rel = tmp / "releases"
    rel.mkdir()
    make_release(rel, "1.0")
    make_release(rel, "1.1", tweak="\n/* 1.1 */\n")
    import shutil
    shutil.copy(rel / "v1.1.tar.gz", rel / "stable.tar.gz")   # "stable" points at 1.1

    remote, app = tmp / "remote.git", tmp / "app"
    for d in (remote, app):
        d.mkdir()
        shutil.chown(d, OWNER, OWNER)
    sh(["git", "init", "-q", "--bare", "-b", "main", str(remote)], user=OWNER)
    sh(["git", "init", "-q", "-b", "main"], cwd=app, user=OWNER)
    (app / "app.py").write_text("print('app')\n")
    shutil.chown(app / "app.py", OWNER, OWNER)
    g = lambda *a: sh(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=app, user=OWNER)
    g("add", "-A")
    g("commit", "-q", "-m", "init")
    g("remote", "add", "origin", str(remote))
    g("push", "-q", "-u", "origin", "main")

    srv = http.server.HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state_dir = tmp / "state"
    cfg_path = tmp / "testapp.toml"
    cfg_path.write_text(f'''app = "Test app"
repo = "{app}"
state_dir = "{state_dir}"
channel = "v1.0"
source = "file://{rel}/{{ref}}.tar.gz"
deploy = "touch {tmp}/deployed"
health = "http://127.0.0.1:{srv.server_port}/"
[files]
"css/uniform.css" = "static/design/uniform.css"
"js/design-card.js" = "static/design/design-card.js"
"glyphs/svg/" = "static/design/glyphs/"
"tools/du_flask.py" = "du_flask.py"
''')

    def go(action="run"):
        return du_sync.main(["du_sync.py", action, str(cfg_path)])

    def status():
        return json.loads((state_dir / "status.json").read_text())

    def log():
        return g("log", "--format=%s").stdout.splitlines()

    print("First install of v1.0")
    check("returns 0", go() == 0)
    st = status()
    check("installed v1.0", st.get("installed") == "v1.0", st)
    check("result updated", st.get("result") == "updated", st)
    check("files copied", (app / "static/design/uniform.css").exists() and (app / "static/design/glyphs/home.svg").exists())
    check("files owned by the repo owner", pwd.getpwuid((app / "static/design/uniform.css").stat().st_uid).pw_name == OWNER)
    check("committed", log()[0] == "Update Design Uniformity to v1.0", log())
    check("pushed", sh(["git", "log", "-1", "--format=%s"], cwd=remote, user=OWNER).stdout.strip() == "Update Design Uniformity to v1.0")
    check("deployed", (tmp / "deployed").exists())
    check("not busy after", st.get("busy") is False)

    print("Run again with nothing new")
    (tmp / "deployed").unlink()
    go()
    check("up to date", status().get("result") == "ok" and status().get("message") == "Up to date.", status())
    check("no new commit", len(log()) == 2)
    check("no deploy", not (tmp / "deployed").exists())

    print("Uncommitted changes block an update")
    cfg_path.write_text(cfg_path.read_text().replace('channel = "v1.0"', 'channel = "stable"'))
    (app / "app.py").write_text("print('edited')\n")
    check("returns 1", go() == 1)
    st = status()
    check("blocked", st.get("result") == "blocked" and "app.py" in st.get("message", ""), st)
    check("still v1.0", st.get("installed") == "v1.0")
    check("available says v1.1", st.get("available") == "v1.1", st)
    g("checkout", "--", "app.py")

    print("A failing deploy puts v1.0 back")
    Health.status = 503
    du_sync.HEALTH_WAIT = 6
    check("returns 1", go() == 1)
    st = status()
    check("rolled_back", st.get("result") == "rolled_back", st)
    check("still v1.0", st.get("installed") == "v1.0", st)
    check("file restored", "/* 1.1 */" not in (app / "static/design/uniform.css").read_text())
    check("revert committed", log()[0].startswith("Put Design Uniformity back to v1.0"), log()[:3])
    check("repo clean after", not du_sync.dirty(du_sync.load_config(str(cfg_path))))
    Health.status = 200

    print("Update to v1.1 (stable)")
    check("returns 0", go() == 0)
    st = status()
    check("installed v1.1", st.get("installed") == "v1.1", st)
    check("previous v1.0", st.get("previous") == "v1.0", st)
    check("file is 1.1", "/* 1.1 */" in (app / "static/design/uniform.css").read_text())

    print("Flask adapter")
    try:
        import flask
    except ImportError:
        flask = None
    if flask is None:
        print("  skip Flask isn't installed here")
    else:
        sys.path.insert(0, str(app))
        import du_flask
        fa = flask.Flask("t")
        du_flask.init_design(fa, state_dir)
        c = fa.test_client()
        s = c.get("/api/design/status").get_json()
        check("status shows v1.1", s["version"] == "v1.1" and s["tone"] == "success" and s["can_rollback"], s)
        check("dates in spec format", s["date"] == "4 Nov 2026", s["date"])
        check("form POST refused", c.post("/api/design/check", data={"x": 1}).status_code == 415)

        sd = Path(tempfile.mkdtemp())
        (sd / "design").mkdir()
        (sd / "design/uniform.css").write_text("/* x */")
        os.utime(sd / "design/uniform.css", (1700000000, 1700000000))
        sa = flask.Flask("s", static_folder=str(sd), static_url_path="/static")
        du_flask.init_design(sa, state_dir)
        du_flask.version_static_links(sa)                                   # twice: still one hook
        with sa.test_request_context():
            u = flask.url_for("static", filename="design/uniform.css")
            check("static link versioned", u == "/static/design/uniform.css?v=1700000000", u)
            u = flask.url_for("static", filename="design/uniform.css", v="2")
            check("an explicit v= kept", u == "/static/design/uniform.css?v=2", u)
            u = flask.url_for("static", filename="missing.css")
            check("missing file: plain link", u == "/static/missing.css", u)
        sa = flask.Flask("o", static_folder=str(sd), static_url_path="/static")
        du_flask.init_design(sa, state_dir, version_static=False)
        with sa.test_request_context():
            u = flask.url_for("static", filename="design/uniform.css")
            check("version_static=False: plain link", u == "/static/design/uniform.css", u)
        r = c.post("/api/design/auto", json={"enabled": False}).get_json()
        check("auto off saved", r["auto"] is False and json.loads((state_dir / "prefs.json").read_text())["auto"] is False)
        r = c.post("/api/design/rollback", json={}).get_json()
        check("rollback flag dropped", (state_dir / "rollback").exists() and r["busy"])

        print("The flag runs a rollback (as the path unit would)")
        check("returns 0", go("flagged") == 0)
        st = status()
        check("back on v1.0", st.get("installed") == "v1.0", st)
        check("previous now v1.1", st.get("previous") == "v1.1", st)
        check("auto turned off", st.get("auto") is False)
        check("flag cleared", not (state_dir / "rollback").exists())
        check("rollback committed", log()[0] == "Roll back Design Uniformity to v1.0", log()[:2])

        print("Nightly run with auto off only reports")
        check("returns 0", go("flagged") == 0)
        st = status()
        check("available, not installed", st.get("result") == "available" and st.get("installed") == "v1.0", st)

        print("Check now installs it anyway")
        c.post("/api/design/check", json={})
        go("flagged")
        check("installed v1.1", status().get("installed") == "v1.1", status())

    print("A tampered release is refused")
    bad = rel / "stable.tar.gz"
    with tarfile.open(bad) as t:
        members = [(m, t.extractfile(m).read()) for m in t.getmembers()]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for m, d in members:
            if m.name.endswith("css/uniform.css"):
                d += b"/* evil */"
                m.size = len(d)
            t.addfile(m, io.BytesIO(d))
    bad.write_bytes(buf.getvalue())
    (app / "static/design/uniform.css").write_text("x")      # force it to want an update
    g("commit", "-q", "-am", "local edit")
    check("returns 1", go() == 1)
    check("failed with manifest message", "doesn't match" in status().get("message", ""), status())
    check("file not replaced", (app / "static/design/uniform.css").read_text() == "x")

    srv.shutdown()
    shutil.rmtree(tmp)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
