window.MarkdownMini = {
  escape(s){ return String(s || "").replace(/[&<>]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;" }[c])); },
  inline(s){
    return this.escape(s)
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" data-external-link>$1</a>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>');
  },
  table(lines){
    const rows = lines
      .filter(line => !/^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line))
      .map(line => line.trim().replace(/^\||\|$/g, '').split('|').map(cell => this.inline(cell.trim())));
    if(!rows.length) return '';
    const [head, ...body] = rows;
    return `<table><thead><tr>${head.map(cell=>`<th>${cell}</th>`).join('')}</tr></thead><tbody>${body.map(row=>`<tr>${row.map(cell=>`<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  },
  render(md){
    const lines = String(md || "").replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let list = [];
    const flushList = () => {
      if(list.length){ out.push(`<ul>${list.map(item=>`<li>${item}</li>`).join('')}</ul>`); list = []; }
    };
    for(let i=0;i<lines.length;i++){
      const raw = lines[i];
      const line = raw.trim();
      if(!line){ flushList(); continue; }
      if(line.includes('|') && /^\|?[^|]+\|/.test(line)){
        flushList();
        const tableLines = [];
        while(i < lines.length && lines[i].trim().includes('|')){
          tableLines.push(lines[i].trim());
          i++;
        }
        i--;
        out.push(this.table(tableLines));
        continue;
      }
      const heading = line.match(/^(#{1,3})\s+(.+)$/);
      if(heading){
        flushList();
        out.push(`<h${heading[1].length}>${this.inline(heading[2])}</h${heading[1].length}>`);
        continue;
      }
      const bullet = line.match(/^[-*]\s+(.+)$/);
      if(bullet){ list.push(this.inline(bullet[1])); continue; }
      if(line.startsWith('>')){
        flushList();
        out.push(`<blockquote>${this.inline(line.replace(/^>\s*/, ''))}</blockquote>`);
        continue;
      }
      flushList();
      out.push(`<p>${this.inline(line)}</p>`);
    }
    flushList();
    return out.join("");
  }
};
