/* idea-research UI icon catalog — Phase 5.
 *
 * Tabler-flavoured outline icons; deliberately small (no Tabler dep).
 * Each icon is an SVG string drop-in for ``dangerouslySetInnerHTML``.
 */
(function () {
  "use strict";

  function svg(d, opts) {
    var w = (opts && opts.w) || 20;
    var h = (opts && opts.h) || 20;
    return (
      '<svg viewBox="0 0 24 24" width="' + w + '" height="' + h +
      '" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' +
      d + "</svg>"
    );
  }

  var ICONS = {
    radar: svg('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12 L20 6"/>'),
    breakdown: svg('<path d="M4 6h16M4 12h10M4 18h7"/>'),
    compare: svg('<path d="M8 3v18M16 3v18M3 12h18"/>'),
    script: svg('<path d="M5 4h11l3 3v13H5z"/><path d="M9 9h6M9 13h6M9 17h4"/>'),
    cookie: svg('<circle cx="12" cy="12" r="9"/><circle cx="9" cy="9" r="1"/><circle cx="15" cy="11" r="1"/><circle cx="11" cy="15" r="1"/>'),
    memory: svg('<path d="M4 6h16v12H4z"/><path d="M8 10v4M12 10v4M16 10v4"/>'),
    chevron: svg('<path d="M9 6 L15 12 L9 18"/>'),
    chevronDown: svg('<path d="M6 9 L12 15 L18 9"/>'),
    refresh: svg('<path d="M3 12a9 9 0 0 1 15.5-6.4"/><path d="M21 4v5h-5"/><path d="M21 12a9 9 0 0 1-15.5 6.4"/><path d="M3 20v-5h5"/>'),
    play: svg('<path d="M8 5l11 7-11 7z"/>'),
    cancel: svg('<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>'),
    trash: svg('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>'),
    check: svg('<path d="M5 12l5 5 9-11"/>'),
    save: svg('<path d="M5 4h11l3 3v13H5z"/><path d="M7 4v6h9V4M7 14h10v6H7z"/>'),
    handoff: svg('<path d="M5 12h12"/><path d="M13 6l6 6-6 6"/>'),
    sparkle: svg('<path d="M12 3v3M12 18v3M3 12h3M18 12h3"/><path d="M6 6l2 2M16 16l2 2M16 8l2-2M6 18l2-2"/>'),
    cog: svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 9 19.4a1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
    list: svg('<path d="M4 6h16M4 12h16M4 18h16"/>'),
    cloud: svg('<path d="M7 18a4 4 0 0 1 .4-7.97A6 6 0 0 1 19 11a4 4 0 0 1-1 7.93z"/>'),
    box: svg('<path d="M3 7l9-4 9 4v10l-9 4-9-4z"/><path d="M3 7l9 4 9-4M12 11v10"/>'),
    info: svg('<circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v5h1"/>'),
    alert: svg('<path d="M12 3l10 18H2z"/><path d="M12 9v5M12 18h.01"/>'),
    eye: svg('<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'),
    plus: svg('<path d="M12 5v14M5 12h14"/>'),
    download: svg('<path d="M12 4v12M6 12l6 6 6-6"/><path d="M4 20h16"/>'),
  };

  function get(name) { return ICONS[name] || ""; }

  window.OpenAkita = window.OpenAkita || {};
  window.OpenAkita.icons = { get: get, list: Object.keys(ICONS) };
})();
