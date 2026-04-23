/**
 * OpenAkita Plugin UI Kit — icons.js
 *
 * Inline SVG icon factory. Avoids emoji (per design rule: no emoji in UI;
 * everything must be a real SVG so we keep visual control across themes,
 * platforms, and small sizes).
 *
 * Usage in plugin HTML:
 *
 *   <script src="/api/plugins/_sdk/ui-kit/icons.js"></script>
 *   ...
 *   <span class="oa-icon" style="color:#f59e0b">
 *     <!-- inject via innerHTML / dangerouslySetInnerHTML -->
 *   </span>
 *
 *   const html = OpenAkitaIcons.warning();   // returns SVG string (no <span>)
 *   el.innerHTML = `<span class="oa-icon">${html}</span>`;
 *
 * All icons are 24x24 viewBox, stroke-based (currentColor), so they inherit
 * text color and scale via font-size. They are intentionally small (≈ 200B
 * each) so the file stays under ~3KB total.
 *
 * Idempotent: safe to load multiple times.
 */
(function () {
  if (typeof window === "undefined") return;
  if (window.OpenAkitaIcons) return;

  // Common SVG attributes baked into a helper to keep individual icons short.
  function svg(inner) {
    return (
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' +
      'fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      inner +
      "</svg>"
    );
  }

  const I = {
    warning: function () {
      return svg(
        '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>' +
        '<line x1="12" y1="9" x2="12" y2="13"/>' +
        '<line x1="12" y1="17" x2="12.01" y2="17"/>'
      );
    },
    info: function () {
      return svg(
        '<circle cx="12" cy="12" r="10"/>' +
        '<line x1="12" y1="16" x2="12" y2="12"/>' +
        '<line x1="12" y1="8" x2="12.01" y2="8"/>'
      );
    },
    arrowRight: function () {
      return svg(
        '<line x1="5" y1="12" x2="19" y2="12"/>' +
        '<polyline points="12 5 19 12 12 19"/>'
      );
    },
    chevronDown: function () {
      return svg('<polyline points="6 9 12 15 18 9"/>');
    },
    chevronRight: function () {
      return svg('<polyline points="9 18 15 12 9 6"/>');
    },
    check: function () {
      return svg('<polyline points="20 6 9 17 4 12"/>');
    },
    close: function () {
      return svg(
        '<line x1="18" y1="6" x2="6" y2="18"/>' +
        '<line x1="6" y1="6" x2="18" y2="18"/>'
      );
    },
    pin: function () {
      return svg(
        '<line x1="12" y1="17" x2="12" y2="22"/>' +
        '<path d="M5 17h14l-1.7-7a2 2 0 0 0-1.95-1.5h-6.7A2 2 0 0 0 6.7 10z"/>' +
        '<line x1="9" y1="6" x2="15" y2="6"/>' +
        '<line x1="12" y1="2" x2="12" y2="6"/>'
      );
    },
    key: function () {
      return svg(
        '<path d="M21 2l-2 2m-7.6 7.6a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78z"/>' +
        '<path d="M15.5 7.5l3 3L22 7l-3-3"/>'
      );
    },
    settings: function () {
      return svg(
        '<circle cx="12" cy="12" r="3"/>' +
        '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>'
      );
    },
    folder: function () {
      return svg(
        '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>'
      );
    },
    image: function () {
      return svg(
        '<rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>' +
        '<circle cx="8.5" cy="8.5" r="1.5"/>' +
        '<polyline points="21 15 16 10 5 21"/>'
      );
    },
    video: function () {
      return svg(
        '<polygon points="23 7 16 12 23 17 23 7"/>' +
        '<rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>'
      );
    },
    palette: function () {
      return svg(
        '<circle cx="13.5" cy="6.5" r="0.5"/>' +
        '<circle cx="17.5" cy="10.5" r="0.5"/>' +
        '<circle cx="8.5" cy="7.5" r="0.5"/>' +
        '<circle cx="6.5" cy="12.5" r="0.5"/>' +
        '<path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.83 0 1.5-.67 1.5-1.5 0-.39-.15-.74-.39-1.01-.23-.26-.38-.61-.38-.99 0-.83.67-1.5 1.5-1.5H16c3.31 0 6-2.69 6-6 0-4.96-4.49-9-10-9z"/>'
      );
    },
    bell: function () {
      return svg(
        '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/>' +
        '<path d="M13.73 21a2 2 0 0 1-3.46 0"/>'
      );
    },
    refresh: function () {
      return svg(
        '<polyline points="23 4 23 10 17 10"/>' +
        '<polyline points="1 20 1 14 7 14"/>' +
        '<path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/>' +
        '<path d="M20.49 15a9 9 0 0 1-14.85 3.36L1 14"/>'
      );
    },
    download: function () {
      return svg(
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>' +
        '<polyline points="7 10 12 15 17 10"/>' +
        '<line x1="12" y1="15" x2="12" y2="3"/>'
      );
    },
    upload: function () {
      return svg(
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>' +
        '<polyline points="17 8 12 3 7 8"/>' +
        '<line x1="12" y1="3" x2="12" y2="15"/>'
      );
    },
    listTask: function () {
      return svg(
        '<line x1="8" y1="6" x2="21" y2="6"/>' +
        '<line x1="8" y1="12" x2="21" y2="12"/>' +
        '<line x1="8" y1="18" x2="21" y2="18"/>' +
        '<polyline points="3 6 4 7 6 5"/>' +
        '<polyline points="3 12 4 13 6 11"/>' +
        '<polyline points="3 18 4 19 6 17"/>'
      );
    },
    book: function () {
      return svg(
        '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>' +
        '<path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>'
      );
    },
    sparkles: function () {
      return svg(
        '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/>'
      );
    },
    cart: function () {
      return svg(
        '<circle cx="9" cy="21" r="1"/>' +
        '<circle cx="20" cy="21" r="1"/>' +
        '<path d="M1 1h4l2.7 13.4a2 2 0 0 0 2 1.6h9.7a2 2 0 0 0 2-1.6L23 6H6"/>'
      );
    },
    mic: function () {
      return svg(
        '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>' +
        '<path d="M19 10v2a7 7 0 0 1-14 0v-2"/>' +
        '<line x1="12" y1="19" x2="12" y2="23"/>' +
        '<line x1="8" y1="23" x2="16" y2="23"/>'
      );
    },
    headphones: function () {
      return svg(
        '<path d="M3 18v-6a9 9 0 0 1 18 0v6"/>' +
        '<path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3z"/>' +
        '<path d="M3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/>'
      );
    },
    edit: function () {
      return svg(
        '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>' +
        '<path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4z"/>'
      );
    },
    scissors: function () {
      return svg(
        '<circle cx="6" cy="6" r="3"/>' +
        '<circle cx="6" cy="18" r="3"/>' +
        '<line x1="20" y1="4" x2="8.12" y2="15.88"/>' +
        '<line x1="14.47" y1="14.48" x2="20" y2="20"/>' +
        '<line x1="8.12" y1="8.12" x2="12" y2="12"/>'
      );
    },
    brush: function () {
      return svg(
        '<path d="M9.06 11.9 16 5l3 3-6.9 6.94a2 2 0 0 1-2.83 0l-.21-.2a2 2 0 0 1 0-2.84z"/>' +
        '<path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.21 0 4-1.79 4-4.04a3.01 3.01 0 0 0-3-3.02z"/>'
      );
    },
    film: function () {
      return svg(
        '<rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/>' +
        '<line x1="7" y1="2" x2="7" y2="22"/>' +
        '<line x1="17" y1="2" x2="17" y2="22"/>' +
        '<line x1="2" y1="12" x2="22" y2="12"/>' +
        '<line x1="2" y1="7" x2="7" y2="7"/>' +
        '<line x1="2" y1="17" x2="7" y2="17"/>' +
        '<line x1="17" y1="17" x2="22" y2="17"/>' +
        '<line x1="17" y1="7" x2="22" y2="7"/>'
      );
    },
    target: function () {
      return svg(
        '<circle cx="12" cy="12" r="10"/>' +
        '<circle cx="12" cy="12" r="6"/>' +
        '<circle cx="12" cy="12" r="2"/>'
      );
    },
    globe: function () {
      return svg(
        '<circle cx="12" cy="12" r="10"/>' +
        '<line x1="2" y1="12" x2="22" y2="12"/>' +
        '<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>'
      );
    },
    // Required by the new Settings layout (mirrors seedance-video).
    // Using stroke-only 24x24 paths so they inherit currentColor and
    // align visually with every other icon in the kit.
    package: function () {
      return svg(
        '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>' +
        '<polyline points="3.27 6.96 12 12.01 20.73 6.96"/>' +
        '<line x1="12" y1="22.08" x2="12" y2="12"/>'
      );
    },
    trash: function () {
      return svg(
        '<polyline points="3 6 5 6 21 6"/>' +
        '<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>' +
        '<path d="M10 11v6"/>' +
        '<path d="M14 11v6"/>' +
        '<path d="M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/>'
      );
    },
    link: function () {
      return svg(
        '<path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.5 1.5"/>' +
        '<path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.5-1.5"/>'
      );
    },
    folderOpen: function () {
      return svg(
        '<path d="M6 14l1.45-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.55 6a2 2 0 0 1-1.94 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.93a2 2 0 0 1 1.66.9l.82 1.2a2 2 0 0 0 1.66.9H18a2 2 0 0 1 2 2v2"/>'
      );
    },
    // Alias warning -> warn so seedance-style <Ico name="warn"/> just works.
    warn: function () {
      return I.warning();
    },
  };

  window.OpenAkitaIcons = I;
})();
