# Design Uniformity

The shared look for Marc's web apps, taken from Binge Stats.

- **Read first:** `DESIGN-SPEC.md` for how apps look, `APP-STANDARD.md` for how they're built, run and deployed, `API-STANDARD.md` for the API other things read them through (displays, Docket, Home Assistant), and `PWA-ICONS.md` for home screen icons and the web app manifest.
- **See it:** open `preview.html` in a browser. It works straight from disk. Switch the theme and accent at the top, and click any glyph to copy its SVG.

## Using it in an app

**Plain HTML / Flask without a build step** (e.g. Vital Signs):

```html
<link rel="stylesheet" href="/static/design/uniform.css">
<link rel="stylesheet" href="/static/design/accents.css">   <!-- only if the app isn't Media (violet) -->
<html class="accent-teal" data-theme="dark">                 <!-- theme set by partials/theme_head.html -->
```

Glyphs: copy `glyphs/icons.svg` to static, then `<svg class="icon"><use href="/static/icons.svg#home"/></svg>`. Or use `js/icons.js` and its `icon('home')`.

**Tailwind (standalone CLI)**, as in Binge Stats:

```js
// tailwind.config.js
module.exports = { presets: [require("./design/tailwind.preset.js")], content: ["./app/templates/**/*.html", "./app/static/js/**/*.js"] };
```

Start the app's `input.css` with `css/tailwind-components.css`.

**Jinja templates:** copy `templates/macros/icons.html`, `templates/macros/ui.html`, `templates/base.html` and `templates/partials/` in.

**Telling Claude Code:** in the new app's CLAUDE.md, add:

> UI follows `C:/ClaudeCowork/Design Uniformity/DESIGN-SPEC.md` (a copy is in `docs/design/`). Use its tokens, components and glyphs; no one-off styles.

## Keeping apps up to date

This folder is the repo `github.com/marctew/design-uniformity`. Apps follow its `stable` tag:

```
python tools/release.py 1.1           # tag v1.1 (no app changes yet)
python tools/release.py promote 1.1   # point stable at it: apps update tonight, or on Check now
```

On an app's host: write `/etc/design-uniformity/<app>.toml` (example in `DESIGN-SPEC.md` 18.1), then
`sudo python3 tools/du_sync.py install /etc/design-uniformity/<app>.toml`. In a Flask app:
`from du_flask import init_design; init_design(app, "<state_dir>")` and include `partials/design_card.html` in Settings.

## Contents

```
DESIGN-SPEC.md                 the spec
APP-STANDARD.md                how apps are built: Flask package, gunicorn, systemd, /healthz, update.sh
API-STANDARD.md                the outside API: /api/v1 status, feeds, images, actions, events, display pages
PWA-ICONS.md                   home screen icons, manifest.json and full-screen behaviour on phones
preview.html                   everything on one page
css/uniform.css                plain CSS: tokens + components
css/accents.css                accent families, one per category (blue, graphite, teal, fuchsia)
css/tailwind.preset.js         Tailwind preset
css/tailwind-components.css    Tailwind entry: tokens + components
glyphs/svg/                    73 glyphs, one file each
glyphs/icons.svg               the same as a sprite
glyphs/glyphs.json             name -> SVG, grouped
templates/base.html            app shell (sidebar, top bar, bottom nav)
templates/macros/icons.html    Jinja icon() macro
templates/macros/ui.html       Jinja components
templates/partials/            theme head, theme switch, toast, design card, sidebar toggle and footer, About page,
                               API tokens and Webhooks cards for Settings
templates/display/base.html    the display page shell (API-STANDARD 9)
js/icons.js, toast.js, chart-palette.js, design-card.js, confirm.js (inline confirm for plain forms),
                               sidebar.js (the sidebar's Collapse button, 6.1),
                               fit-screen.js (full-height pages on phones, PWA-ICONS 3.1),
                               scroll-fade.js (fades a sideways row's edge, 7.17)
brand/                         each app's icon, the app-icon template, teal example
tools/                         du_sync.py (updater), du_flask.py (Settings card API, versioned static links), du_api.py (the outside API),
                               check_api.py (checks an app against API-STANDARD), check_du_api.py (checks du_api.py
                               itself), release.py, check_du_sync.py
```
