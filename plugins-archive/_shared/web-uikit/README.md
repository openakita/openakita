# web-uikit (archived from SDK 0.6.x)

This directory contains the legacy frontend assets that used to live at
`openakita-plugin-sdk/src/openakita_plugin_sdk/web/`. They were bundled into
the SDK wheel and served by the host at `/api/plugins/_sdk/*` during the
0.3.x – 0.6.x "Plugin 2.0 UI" expansion phase.

In **SDK 0.7.0** the plugin SDK was refocused back to its original "minimal
shell" design (pure Python, stdlib + optional pydantic, no frontend, no host
coupling). The host no longer mounts `/api/plugins/_sdk/*` and the SDK wheel
no longer ships any frontend files.

## What's here

```
web-uikit/
├── bootstrap.js              # iframe ↔ host bridge (theme/locale/api)
└── ui-kit/
    ├── styles.css            # shared oa-* CSS tokens & primitives
    ├── icons.js              # OpenAkitaIcons.{name}() inline SVG helper
    ├── i18n.js               # OpenAkitaI18n.t() + bridge.* dictionary
    └── markdown-mini.js      # OpenAkitaMarkdown tiny renderer
```

> Several other ui-kit modules (`cost-preview.js`, `dep-gate.js`,
> `error-coach.js`, `event-helpers.js`, `first-success-celebrate.js`,
> `onboard-wizard.js`, `task-panel.js`) were already removed during the
> earlier `b3b5f02d` cleanup pass; only the four still consumed by tongyi /
> seedance survived.

## Status

- **Not redistributed**: the SDK wheel does not include this directory.
- **Not mounted**: the host (`src/openakita/api/server.py`) no longer
  serves these files at any URL.
- **Reference only**: kept in the archive so that the 19 archived plugins
  (`plugins-archive/<name>/`) and any external community fork can copy the
  exact same byte-for-byte assets they used to receive from the host.

## How to revive a plugin's UI

If you are reviving an archived plugin and want its old `ui/dist/index.html`
to work again on a 0.7.0+ host, follow the pattern used by the two
first-class plugins:

1. Create `plugins/<your-plugin>/ui/dist/_assets/`.
2. Copy whichever of the files above your HTML actually references (e.g.
   `bootstrap.js` is needed for theme/locale sync; `styles.css` + `icons.js`
   are needed if you use `oa-*` classes or `OpenAkitaIcons`).
3. Rewrite the `<script>` / `<link>` tags from `/api/plugins/_sdk/...` to
   relative `_assets/...` paths and drop the `?v=...` cache-busting params.
4. (Optional) Drop any references to retired widgets like `OpenAkitaDepGate`
   — `plugin_deps.py` was removed alongside this move, so the dep-gate
   feature is no longer functional regardless of whether the JS is present.

See `plugins/tongyi-image/ui/dist/_assets/` and
`plugins/seedance-video/ui/dist/_assets/` for working examples.
