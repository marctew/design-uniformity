# API standard

How Marc's apps let other things read and drive them: Docket's briefs, Vital Signs screens, the ESP32 panels, the widget display, Home Assistant, n8n and TRMNL. `APP-STANDARD.md` says how an app is built; this says what its outside API looks like, so a reader written once works against every app.

**Status:** draft 1, 5 Nov 2026. Not released to the apps yet. Decisions taken with Marc on 5 Nov 2026 are marked **(decided)**.

## 1. What it's for

| Reader | Where it runs | What it needs |
|---|---|---|
| **Docket briefs** (Claude, via Docket's `feed_fetch`) | Docket's server, on the LAN | Feeds and status, read-only. The token stays in Docket; Claude never sees it. |
| **Vital Signs** pages | Its server's data proxy, then Chromium on the Pis | Status, feeds, images, display pages; live updates for alerts. |
| **ESP32 panels** (through the PC panel agent) | The PC | Small JSON: short labels, numbers, pre-formatted times. Actions for buttons. |
| **Home Assistant** | LAN | Status as REST sensors, actions as REST commands, webhooks for automations. |
| **TRMNL** and other cloud displays | TRMNL's cloud, polling over the internet | Small JSON with the token in the URL. Only for apps Marc puts on the tunnel. |
| **The widget display** and any kiosk | A browser on the LAN | Display pages that just need a URL. |

What it isn't: a replacement for an app's own page JSON. Each app keeps its private `/api/...` routes for its own UI, with the session cookie and CSRF (`APP-STANDARD.md` 4). The public contract lives only under **`/api/v1/`**, plus `/display/` and `/healthz`.

## 2. The shape of it

Every app that adopts the standard has these. Anything an app doesn't have is simply missing from `meta`, not an error.

| Path | Method | Token | What |
|---|---|---|---|
| `/healthz` | GET | none | Unchanged from `APP-STANDARD.md`: `{"ok": true, "busy": [...]}`. Says nothing about contents. |
| `/api/v1/meta` | GET | read | Who the app is and what it offers: version, accent, icon, and the list of feeds, actions, displays and events. Readers use it to build pickers. |
| `/api/v1/status` | GET | read | The one-glance summary: a state, a headline and a few numbers. Every display and HA sensor starts here. |
| `/api/v1/feeds/<name>` | GET | read | A list of items in the common item shape (section 5). |
| `/api/v1/images/<kind>/<id>` | GET | read | Artwork or a thumbnail, resized by the app. |
| `/api/v1/actions/<name>` | POST | write | One named thing to do, with a JSON body. |
| `/api/v1/events` | GET | read | Server-sent events: live changes for screens. |
| `/display/<name>` | GET | read | A ready-made HTML card in the app's look, for kiosks and the widget display. |

Readers send `Accept: application/json` (except for images, events and display pages). Every JSON answer is UTF-8, `snake_case`, and is an object at the top level, never a bare list, so a field can be added later without breaking anyone.

## 3. Tokens

- **Made in the app's Settings,** on an **API tokens** card (a shared partial in this repo), with a name ("Vital Signs desk", "TRMNL kitchen") and a scope: **read** or **write**. A write token can also read.
- **Shown once,** stored only as a SHA-256 hash, with `created_at` and `last_used_at` shown on the card. Revoke is one click. This is how Binge Stats already does it; it's the model.
- **Sent as** `Authorization: Bearer <token>`.
- **Read tokens may also go in the URL** as `?token=…` **(decided)**, because TRMNL's cloud polling and simple firmware can't always set a header. Write tokens are refused in the URL with `400 {"error": "Write tokens go in the Authorization header, never the URL."}`. URLs end up in logs, so a URL token should be a display's own read token, easy to revoke. Apps never log query strings for `/api/v1/` and `/display/`.
- **A signed-in browser** (the app's own session cookie) can read every `/api/v1/` GET too, so the app's own pages and its `/display/` previews work without a token. Writes from a session still need CSRF.
- **Bad tokens:** `401 {"error": "That token isn't valid. Make a new one in Settings → API tokens."}`. Ten bad tokens from one address in a minute lock that address out for 15 minutes with `429` and `Retry-After`, the same rule as Docket's MCP. A good token with the wrong scope gets `403`.
- **Secrets an app holds for others** (Plex tokens, indexer keys, a mail password) never appear in any `/api/v1/` answer, image URL or display page.

## 4. Exposure

- **LAN by default (decided).** `/api/v1/`, `/display/` and `/healthz` answer on the app's LAN address.
- **Through the tunnel only per app, by Marc's choice (decided).** If an app's hostname is on the Cloudflare tunnel, its `/api/v1/` works there too, always with a token. An app can switch that off with `API_PUBLIC=0` in its env, which makes it refuse `/api/v1/` and `/display/` requests that came through the proxy (`CF-Connecting-IP` present) with `403`.
- **No CORS by default.** Browser readers on another origin go through a server (Vital Signs' data proxy). An app may list allowed origins in Settings if one is ever needed.

## 5. Common shapes

### Status

```json
{
  "app": "encore",
  "state": "ok",
  "headline": "2 albums downloading",
  "detail": "Next: Rumours (1977), 64%",
  "counts": [
    {"key": "downloading", "label": "Downloading", "value": 2},
    {"key": "queued", "label": "Queued", "value": 5},
    {"key": "failed_today", "label": "Failed today", "value": 0, "tone": "danger"}
  ],
  "url": "https://encore.marctew.co.uk/",
  "generated_at": "2026-11-05T22:40:00Z",
  "refresh_s": 30
}
```

- `state` is one of `ok`, `busy`, `warning`, `error`, `off`. It maps straight to the design's tones (success, info, warning, danger, muted) so every screen colours it the same.
- `headline` is one short sentence that makes sense on its own on a 2-inch screen. `detail` is optional and can be longer.
- `counts` is at most 6 entries. `key` is stable for machines (HA sensors read `counts[key=...]`), `label` is for people. `tone` is optional.
- `refresh_s` is how often a poller should come back. Readers that can't do events use it.

### Feeds and items

```json
{
  "feed": "arrivals",
  "title": "Just arrived",
  "items": [
    {
      "id": "plex:58213",
      "title": "Slow Horses",
      "subtitle": "S5 E3 · Missing Persons",
      "at": "2026-11-05T19:12:00Z",
      "at_label": "Today 19:12",
      "tone": "accent",
      "image": "/api/v1/images/poster/58213",
      "url": "http://192.168.4.57:8181/shows/412",
      "meta": {"show_id": 412, "season": 5, "episode": 3}
    }
  ],
  "next_cursor": null,
  "generated_at": "2026-11-05T22:40:00Z",
  "refresh_s": 300
}
```

- Every item has `id` (stable, unique within the feed) and `title`. Everything else is optional, but when a field fits one of these names it uses it.
- `at` is UTC ISO 8601 ending in `Z`. `at_label` is the same moment already written for a person in the app's time zone ("Today 19:12", "Sat 7 Nov", "in 2 h"), because an ESP32 or a Liquid template shouldn't have to do time zones.
- `image` is a path on the same app (section 6), never a third-party URL that carries a token.
- `url` opens the thing in the app, for a tap on a screen or a link in a brief.
- `meta` holds the feed's own fields. It's documented per feed and is where app-specific detail goes, so the top level stays the same for everyone.
- **Query parameters** every feed accepts: `limit` (default 20, at most 100), `since` (ISO time: items with `at` after it), `cursor` (from `next_cursor`). A feed may add its own, listed in `meta`.

### Errors

```json
{"error": "There's no feed called 'arivals'.", "code": "not_found"}
```

`error` is a sentence for a person (`APP-STANDARD.md` 4). `code` is for machines: `bad_request`, `unauthorised`, `forbidden`, `not_found`, `rate_limited`, `busy`, `upstream` (the app couldn't reach Plex, SABnzbd...), `error`. `429` and `503` always carry `Retry-After`.

### Meta

```json
{
  "app": "binge-stats",
  "name": "Binge Stats",
  "version": "1.0.0",
  "sha": "ba66f41",
  "api": 1,
  "accent": "violet",
  "icon": "/static/icons/icon-192.png",
  "time_zone": "Europe/London",
  "feeds": [{"name": "arrivals", "title": "Just arrived", "refresh_s": 300, "params": {"kind": "movie | show"}}],
  "actions": [{"name": "mark_watched", "title": "Mark watched", "body": {"item_id": "int"}}],
  "displays": [{"name": "now-playing", "title": "Now playing", "sizes": ["320x240", "800x480", "1920x1080"]}],
  "events": ["arrival", "playback.start", "playback.stop"]
}
```

## 6. Images

- `GET /api/v1/images/<kind>/<id>?w=320` (or `h=`). The app fetches the original with its own credentials, resizes it, and answers `image/webp` (or `image/jpeg` if the reader's `Accept` doesn't list WebP, for ESP32 decoders).
- Widths snap to 80, 160, 320, 640 and 1280, so caches stay small. Resized images are cached on disk in `/var/lib/<app>/image-cache/` and served with `Cache-Control: public, max-age=86400` and an ETag.
- `?fmt=jpeg&q=70` is allowed for devices that need it. `?gray=1` gives greyscale for e-ink.
- An image path in a feed works with the same token the feed was read with, so a display can load it straight away.

## 7. Actions

- `POST /api/v1/actions/<name>` with a JSON body. Write token only, in the header.
- **`Idempotency-Key`** header (any string up to 100 characters): repeating a request with the same key within 24 hours gives the first answer again and does nothing twice. Panels and HA retry, so this matters.
- Answer: `{"ok": true, "message": "Queued Rumours (1977).", "result": {...}}`. `message` is short enough for a toast or a panel's status line.
- Only actions listed in `meta` exist. They're named for what they do (`add_task`, `queue_album`, `mark_watched`, `skip_track`), not generic `update` endpoints, and each is documented with its body.
- Anything destructive in the app's own UI (delete, restore) isn't offered as an action unless Marc asks for that one specifically.
- Every action is logged in the app's activity log with the token's name, so "who did that" always has an answer.

## 8. Live updates (decided: push from the start)

### Events stream

- `GET /api/v1/events` is **server-sent events** (`text/event-stream`). Read token (URL or header).
- Each event: `event: <type>`, `id: <increasing number>`, `data: {"type": "...", "at": "...Z", "title": "...", "item": {...item shape...}}`. Types are dotted and listed in `meta` (`arrival`, `download.finished`, `task.due`, `watch.fired`).
- `: ping` every 25 seconds, so proxies don't close it.
- Reconnecting with `Last-Event-ID` replays what was missed from a short in-memory ring (the last 200 events or 1 hour).
- **Capacity:** gunicorn has one worker and a fixed thread pool, so an app allows at most **8 open streams** and answers `503` with `Retry-After: 30` beyond that. Each stream uses one thread; apps that adopt events raise `threads` by 8.
- Through Cloudflare, streams work but are closed after about 100 seconds without data; the 25-second ping prevents that.

### Webhooks out

For Home Assistant and n8n, which handle incoming webhooks better than streams:

- Settings → **Webhooks**: a URL, the event types to send, and a secret.
- Docket-style delivery: each event is POSTed as JSON (the same `data` as the stream), with `X-Signature: sha256=<HMAC of the body with the secret>`, retried 3 times over 10 minutes, with failures shown in the app's activity log.
- Home Assistant's own webhook trigger (`/api/webhook/<id>`) is the expected target. Its id is the secret there, so the URL is stored like a password and never shown again in full.

### Push to Marc's phone

Unchanged: apps that need to tell Marc something use the self-hosted ntfy server, each with its own write-only token. Events and webhooks are for machines and screens; ntfy is for Marc.

## 9. Display pages

- `GET /display/<name>` returns a full HTML page in the app's look (DESIGN-SPEC tokens, its accent), sized to fill the viewport, with no sidebar, no login prompt and no scroll bars.
- Parameters: `token` (read), `theme=dark|light` (default dark), `size=800x480` (a hint for layout breakpoints; the page still fills whatever it's given), `scale=1.5` for far-away screens, `static=1` to render once with no JavaScript (e-ink and screenshot services), and `accent=` to override the app's accent so a wall of screens can match.
- Unless `static=1`, the page updates itself from `/api/v1/events` with a fallback poll at the status's `refresh_s`. If the app is down it keeps the last content and shows a small "Last updated 14:02" in faint text, instead of an error page.
- The same data as the JSON, so anything a display page shows is also reachable as JSON for screens that render their own (TRMNL's Liquid, the ESP32s).
- Vital Signs can show any display page as a normal web page, and can list them from `meta` to offer them as content.

## 10. Caching and politeness

- Every GET answers with an `ETag`. A reader sending `If-None-Match` gets `304` with no body. Polling every 30 seconds then costs almost nothing.
- `Cache-Control: private, max-age=<refresh_s / 2>` on status and feeds.
- Answers aim to be small: status under 2 KB, a feed page under 50 KB. The ESP32s parse JSON in a few hundred KB of RAM.
- An app that's busy (an import) still answers status quickly from what it has, with `state: "busy"`, rather than blocking.
- Per-token limit: 120 requests a minute, then `429` with `Retry-After`. Images and events don't count towards it.

## 11. Versioning

- `api: 1` in `meta`. Within v1, changes are **additive only**: new feeds, new fields, new actions, new event types. Readers ignore fields they don't know.
- A breaking change means `/api/v2/` alongside v1 for at least a month, with v1 marked `"deprecated": "2027-01-01"` in `meta`.
- Each app documents its endpoints in `docs/API.md`: every feed with its `meta` fields, every action with its body, every event type, and every display. That file is what Claude reads before writing a reader.

## 12. Shared code

So each app doesn't write this from scratch, this repo gets (to build):

- **`tools/du_api.py`**, a Flask adapter like `du_flask.py`, carried by the design updater:
  - `init_api(app, data_dir, name=..., version=...)` registers `/api/v1/meta`, token checking, the URL-token rule, the lockout, ETags, error answers and query-string-free logging;
  - decorators `@feed("arrivals", title=..., refresh_s=...)`, `@action("queue_album", body=...)`, `@display("now-playing", sizes=[...])` that also fill `meta`;
  - `publish(event_type, title, item)` for the events stream and webhooks;
  - `image_route(kind, fetch)` with resizing (Pillow) and the disk cache.
- **`templates/partials/api_tokens_card.html`** and **`webhooks_card.html`** for Settings, after the app's own settings and before the Design system card.
- **`templates/display/base.html`**: the display page shell (tokens, theme, the "last updated" note, the events client).
- **`tools/check_api.py <base_url> <token>`**: checks an app against this standard (meta, status shape, a feed's item shape, ETag and 304, a bad token, a URL write token refused) and prints what's wrong. Run it after each app's change, like `check_du_sync.py`.

## 13. Readers

How each reader uses it, once apps have it:

- **Docket:** Settings → **App feeds** holds each app's base URL and a read token (stored like the mail settings, out of the database and backups). A new MCP tool `feed_fetch(app, feed, limit?, since?)` and `app_status(app)` read them server-side, so briefs stop needing claude-ops recipes and Claude never handles a token. This is build step 8.
- **Vital Signs:** a source type `du_app` (base URL and token in `server.toml`) that the data proxy understands, plus "Display page" as content, picked from the app's `meta`.
- **PC panel agent:** reads `status` and feeds with a read token (already does, against Binge's v1), and calls actions for the panel buttons with a separate write token.
- **Home Assistant:** a `rest` sensor per app on `/api/v1/status` (`value_template: "{{ value_json.state }}"`, `json_attributes: [headline, counts]`), `rest_command`s for actions, and webhook triggers for events.
- **TRMNL:** a private plugin polling `https://<app>.marctew.co.uk/api/v1/status?token=…` (and a feed if needed), with a Liquid template. Only for apps on the tunnel.

## 14. Rolling it out

One app at a time, like the design system:

1. **This document,** reviewed by Marc, then released with the shared code (`du_api.py`, the cards, `check_api.py`) as a Design Uniformity version.
2. **Binge Stats:** already has `/api/v1/` and tokens. Bring its answers to the common shapes (keep its current endpoints working, since the media panel uses them), add `meta`, `status`, images, events and a display page.
3. **Docket:** `status` (due today, overdue, the night shift), feeds (`today`, `upcoming`, `reports`), actions (`add_task`, `complete_task`), events (`task.due`, `brief.published`, `watch.fired`, `approval.requested`). Then its reader side (section 13), which is step 8.
4. **Encore** (downloads), **PodBridge** (new episodes, sync state), **Vital Signs** (its own status), **The Back Room** (status only, LAN only, unless Marc wants more).
5. claude-ops is deployed only by Marc and keeps its own API; it isn't part of this unless he says so.

## 15. Open questions

1. **TRMNL:** does Marc have a TRMNL device to target, or is it only the builder for other people? It changes whether display pages need an 800x480 one-bit mode (`static=1&gray=1`) first.
2. **Which display pages first?** Likely candidates: Binge "now playing", Docket "today", Encore "downloads".
3. **HA discovery:** should apps also offer an MQTT discovery option (Stagehand already talks MQTT to HA), or is REST plus webhooks enough?
