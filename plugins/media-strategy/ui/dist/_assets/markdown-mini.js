window.MarkdownMini = {
  escape(s){ return String(s || "").replace(/[&<>]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;" }[c])); },
  render(md){ return String(md || "").split(/\n+/).map(line => `<p>${this.escape(line)}</p>`).join(""); }
};
