/* idea-research UI markdown-mini — Phase 0 placeholder.
 *
 * A tiny Markdown subset renderer (paragraphs, **bold**, *italic*,
 * `code`, lists, headings) used by <BreakdownDetail> in Phase 5 so we
 * never ship a heavyweight Markdown library.
 */
(function () {
  "use strict";

  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, function (c) {
      return (
        {
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        }[c] || c
      );
    });
  }

  function renderInline(text) {
    return escapeHtml(text)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>");
  }

  function render(md) {
    if (!md) return "";
    var lines = String(md).split(/\r?\n/);
    var out = [];
    var inList = false;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var heading = /^(#{1,6})\s+(.*)$/.exec(line);
      var li = /^[\-\*]\s+(.*)$/.exec(line);
      if (heading) {
        if (inList) {
          out.push("</ul>");
          inList = false;
        }
        out.push(
          "<h" +
            heading[1].length +
            ">" +
            renderInline(heading[2]) +
            "</h" +
            heading[1].length +
            ">",
        );
      } else if (li) {
        if (!inList) {
          out.push("<ul>");
          inList = true;
        }
        out.push("<li>" + renderInline(li[1]) + "</li>");
      } else if (line.trim() === "") {
        if (inList) {
          out.push("</ul>");
          inList = false;
        }
      } else {
        if (inList) {
          out.push("</ul>");
          inList = false;
        }
        out.push("<p>" + renderInline(line) + "</p>");
      }
    }
    if (inList) out.push("</ul>");
    return out.join("");
  }

  window.OpenAkita = window.OpenAkita || {};
  window.OpenAkita.markdownMini = { render: render };
})();
