/**
 * OpenAkita Plugin UI Kit — markdown-mini.js
 *
 * Minimal Markdown -> safe HTML renderer (zero dependency, ~80 lines).
 *
 * Supported subset (intentionally tiny — pair with .oa-md-content for type):
 *   - Headings: # ## ### ####
 *   - Unordered lists: -, *, +
 *   - Ordered lists: 1.  2.
 *   - Blockquote: >
 *   - Inline code: `code`
 *   - Bold: **text**
 *   - Italic: *text* (single-asterisk, not nested)
 *   - Links: [label](http(s)://url)  (other schemes are stripped)
 *   - Horizontal rule: ---
 *   - Paragraph + line break (single newline keeps inline; blank line splits)
 *
 * NOT supported (deliberately): images, tables, html passthrough, fenced
 * code blocks beyond inline. If you need them, switch to a real renderer.
 *
 * Output is wrapped in HTML-escaped text first, then a small set of inline
 * tokens are reinserted, so it's safe to display untrusted markdown.
 *
 * Idempotent: safe to load multiple times.
 */
(function () {
  if (typeof window === "undefined") return;
  if (window.OpenAkitaMD) return;

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function inline(text) {
    // Operate on already-escaped text.
    // 1) inline code (must come first to protect contents)
    text = text.replace(/`([^`\n]+)`/g, function (_m, code) {
      return "<code>" + code + "</code>";
    });
    // 2) bold **...**
    text = text.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    // 3) italic *...* (avoid matching ** which is already consumed above)
    text = text.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    // 4) links [label](url) — only http/https/relative
    text = text.replace(
      /\[([^\]\n]+)\]\(([^)\n\s]+)\)/g,
      function (_m, label, url) {
        var safe = /^(https?:\/\/|\/)/i.test(url) ? url : "#";
        return (
          '<a href="' + safe + '" target="_blank" rel="noopener noreferrer">' +
          label +
          "</a>"
        );
      }
    );
    return text;
  }

  function render(src) {
    if (src == null) return "";
    var lines = String(src).replace(/\r\n?/g, "\n").split("\n");
    var out = [];
    var i = 0;
    var inUl = false;
    var inOl = false;
    var paraBuf = [];

    function flushPara() {
      if (paraBuf.length) {
        var joined = paraBuf.map(escapeHtml).join("<br>");
        out.push("<p>" + inline(joined) + "</p>");
        paraBuf = [];
      }
    }
    function closeLists() {
      if (inUl) { out.push("</ul>"); inUl = false; }
      if (inOl) { out.push("</ol>"); inOl = false; }
    }

    while (i < lines.length) {
      var raw = lines[i];
      var line = raw.replace(/\s+$/, "");

      // Blank line: paragraph / list separator
      if (!line.trim()) { flushPara(); closeLists(); i++; continue; }

      // Horizontal rule
      if (/^---+\s*$/.test(line)) {
        flushPara(); closeLists();
        out.push("<hr>");
        i++; continue;
      }

      // Heading
      var hm = /^(#{1,4})\s+(.*)$/.exec(line);
      if (hm) {
        flushPara(); closeLists();
        var level = hm[1].length;
        out.push("<h" + level + ">" + inline(escapeHtml(hm[2])) + "</h" + level + ">");
        i++; continue;
      }

      // Blockquote
      if (/^>\s?/.test(line)) {
        flushPara(); closeLists();
        var qbuf = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          qbuf.push(lines[i].replace(/^>\s?/, ""));
          i++;
        }
        out.push("<blockquote>" + inline(escapeHtml(qbuf.join(" "))) + "</blockquote>");
        continue;
      }

      // Unordered list
      var ulm = /^[-*+]\s+(.*)$/.exec(line);
      if (ulm) {
        flushPara();
        if (inOl) { out.push("</ol>"); inOl = false; }
        if (!inUl) { out.push("<ul>"); inUl = true; }
        out.push("<li>" + inline(escapeHtml(ulm[1])) + "</li>");
        i++; continue;
      }

      // Ordered list
      var olm = /^\d+\.\s+(.*)$/.exec(line);
      if (olm) {
        flushPara();
        if (inUl) { out.push("</ul>"); inUl = false; }
        if (!inOl) { out.push("<ol>"); inOl = true; }
        out.push("<li>" + inline(escapeHtml(olm[1])) + "</li>");
        i++; continue;
      }

      // Plain paragraph line
      closeLists();
      paraBuf.push(line);
      i++;
    }

    flushPara();
    closeLists();
    return out.join("\n");
  }

  window.OpenAkitaMD = { render: render, escapeHtml: escapeHtml };
})();
