window.MarkdownMini = {
  escape(s){ return String(s || "").replace(/[&<>]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;" }[c])); },
  linkify(s){ return s.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>'); },
  render(md){ return String(md || "").split(/\n+/).map(line => `<p>${this.linkify(this.escape(line))}</p>`).join(""); }
};
