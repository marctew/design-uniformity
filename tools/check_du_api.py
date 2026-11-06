#!/usr/bin/env python3
"""Checks for du_api.py's draft 4 parts (v1.11): app tokens and @standard("app"), per-action limits,
meta "inbound", wall-clock times sent in, the reopen rule, and webhook filters. Also that an app passing
nothing new (Binge Stats on v1.10) is unchanged: plain tokens, no "inbound" in meta, no limits.

  python3 tools/check_du_api.py          (needs Flask; any app's venv has it)

Prints one line per check and exits 1 if any fail. Run it before releasing a change to du_api.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flask import Flask, jsonify  # noqa: E402

import du_api  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    failed += 0 if ok else 1


TOKENS = {
    "r": {"id": 1, "name": "Kitchen", "scope": "read"},
    "w": {"id": 2, "name": "HA", "scope": "write"},
    "a": {"id": 3, "name": "Muddy Paws", "scope": "write", "app": "muddy-paws"},
}
now = [1_800_000_000.0]


def make(inbound=True):
    app = Flask(__name__)
    app.config["TESTING"] = True
    core = du_api.init_api(app, name="t", title="T", version="1", sha=lambda: "abc", accent="fuchsia", icon="/i",
                           time_zone=lambda: "Europe/London", check_token=TOKENS.get,
                           is_admin_session=lambda: False, csrf_ok=lambda: True, clock=lambda: now[0], public=True)
    core.add_action("add_task", "Add a task", {"text": "str"})
    if inbound:
        core.add_action("notify", "Send a push", {"title": "str"}, limit="2/hour")
        core.add_inbound("task", {"title": "str", "due": "local?"})

    @app.get("/api/v1/meta")
    @du_api.standard("read")
    def meta():
        return jsonify(du_api.core().meta())

    @app.post("/api/v1/actions/<name>")
    @du_api.standard("write")
    def action(name):
        return du_api.core().over_limit(name) or jsonify(ok=True, who=du_api.identity().name)

    @app.put("/api/v1/inbound/items/<key>")
    @du_api.standard("app")
    def put(key):
        return jsonify(ok=True, app=du_api.identity().app, key=key)

    return app, core


app, core = make()
c = app.test_client()
H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731

# app tokens and @standard("app")
r = c.put("/api/v1/inbound/items/treatment-dog1", headers=H("a"))
check("an app token reaches an inbound route", r.status_code == 200 and r.json["app"] == "muddy-paws", r.data)
r = c.put("/api/v1/inbound/items/x", headers=H("w"))
check("a plain write token gets 403 there", r.status_code == 403 and r.json["code"] == "forbidden"
      and "app's own token" in r.json["error"], r.data)
check("a read token gets 403 there", c.put("/api/v1/inbound/items/x", headers=H("r")).status_code == 403)
check("no token gets 401 there", c.put("/api/v1/inbound/items/x").status_code == 401)
check("an app token is still a write token for actions",
      c.post("/api/v1/actions/add_task", headers=H("a")).status_code == 200)

# per-action limits
codes = [c.post("/api/v1/actions/notify", headers=H("w")).status_code for _ in range(3)]
check("a limited action allows its limit then 429", codes == [200, 200, 429], codes)
r = c.post("/api/v1/actions/notify", headers=H("w"))
check("the 429 has Retry-After and says when", r.headers.get("Retry-After") and "can go at" in r.json["error"], r.data)
check("the limit is per token", c.post("/api/v1/actions/notify", headers=H("a")).status_code == 200)
check("an unlimited action isn't counted",
      all(c.post("/api/v1/actions/add_task", headers=H("w")).status_code == 200 for _ in range(5)))
now[0] += 3601
check("the limit resets after its window", c.post("/api/v1/actions/notify", headers=H("w")).status_code == 200)
try:
    core.add_action("bad", "Bad", {}, limit="10 per hour")
    check("a malformed limit is refused", False)
except ValueError:
    check("a malformed limit is refused", True)

# meta
m = c.get("/api/v1/meta", headers=H("r")).json
check("meta lists inbound", m.get("inbound") == {"kind": "task", "fields": {"title": "str", "due": "local?"}}, m)
check("meta shows an action's limit", next(a for a in m["actions"] if a["name"] == "notify").get("limit") == "2/hour")
check("meta has no limit on unlimited actions", "limit" not in next(a for a in m["actions"] if a["name"] == "add_task"))

# an app passing nothing new is unchanged
app0, _ = make(inbound=False)
m0 = app0.test_client().get("/api/v1/meta", headers=H("r")).json
check("no inbound in meta when the app has none", "inbound" not in m0, m0)
check("tokens without 'app' still work", app0.test_client().post("/api/v1/actions/add_task", headers=H("w")).status_code == 200)

# times sent in
check("local time with a clock", du_api.local_time("2026-12-24 09:00") == ("2026-12-24", "09:00"))
check("local date alone is all day", du_api.local_time("2026-12-24") == ("2026-12-24", None))
for bad in ("2026-12-24T09:00:00Z", "24/12/2026", "2026-02-30", "2026-12-24 25:00", ""):
    try:
        du_api.local_time(bad)
        check(f"refuses {bad!r}", False)
    except ValueError as e:
        check(f"refuses {bad!r}", "YYYY-MM-DD" in str(e) or "real date" in str(e), str(e))
out = du_api.local_out("2026-07-01", "09:00", "Europe/London")
check("local_out gives UTC in summer", out == {"due": "2026-07-01 09:00", "due_at": "2026-07-01T08:00:00Z"}, out)
out = du_api.local_out("2026-12-24", None, "Europe/London")
check("local_out for all day", out == {"due": "2026-12-24", "due_at": "2026-12-24T09:00:00Z"}, out)

# keys and the reopen rule
check("a good key", du_api.valid_key("treatment-dog1-worming") and du_api.valid_key("a:b.c_d"))
check("bad keys", not any(du_api.valid_key(k) for k in ("", "has space", "slash/x", "x" * 101)))
check("done + later due reopens", du_api.reopens("done", ("2026-12-24", "09:00"), ("2027-03-18", "09:00")))
check("done + same due stays done", not du_api.reopens("done", ("2026-12-24", "09:00"), ("2026-12-24", "09:00")))
check("done + earlier due stays done", not du_api.reopens("done", ("2026-12-24", None), ("2026-12-01", None)))
check("open never 'reopens'", not du_api.reopens("open", ("2026-12-24", None), ("2027-01-01", None)))

# webhook filters
ev = {"type": "task.done", "source": {"app": "muddy-paws", "key": "k"}}
check("all events, any source", du_api.webhook_matches([], None, ev))
check("event type filter", not du_api.webhook_matches(["task.due"], None, ev))
check("source filter keeps its own", du_api.webhook_matches(["task.done"], "muddy-paws", ev))
check("source filter drops another app's", not du_api.webhook_matches([], "other", ev))
check("source filter drops events with no source", not du_api.webhook_matches([], "muddy-paws", {"type": "task.done"}))

print(f"\n{'All passed' if not failed else f'{failed} failed'}.")
sys.exit(1 if failed else 0)
