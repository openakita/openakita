window.MarkdownMini = {
  escape(s){ return String(s || "").replace(/[&<>]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;" }[c])); },
  inline(s){
    return this.escape(s)
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" data-external-link>$1</a>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>')
      .replace(/`([^`]+)`/g, '<code>$1</code>');
  },
  isTableSeparator(line){
    return /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(line);
  },
  isTableLine(line){
    return line.includes('|') && (this.isTableSeparator(line) || /^\|?.+\|.+\|?$/.test(line));
  },
  table(lines){
    const rows = lines
      .filter(line => !this.isTableSeparator(line.trim()))
      .map(line => line.trim().replace(/^\||\|$/g, '').split('|').map(cell => this.inline(cell.trim())));
    if(!rows.length) return '';
    const [head, ...body] = rows;
    return `<table><thead><tr>${head.map(cell=>`<th>${cell}</th>`).join('')}</tr></thead><tbody>${body.map(row=>`<tr>${row.map(cell=>`<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  },
  render(md){
    const lines = String(md || "").replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let list = [];
    let listKind = 'ul';
    const flushList = () => {
      if(list.length){ out.push(`<${listKind}>${list.map(item=>`<li>${item}</li>`).join('')}</${listKind}>`); list = []; }
    };
    for(let i=0;i<lines.length;i++){
      const raw = lines[i];
      const line = raw.trim();
      if(!line){ flushList(); continue; }
      if(/^[-*_]{3,}$/.test(line)){
        flushList();
        out.push('<hr>');
        continue;
      }
      if(this.isTableLine(line)){
        flushList();
        const tableLines = [];
        while(i < lines.length && this.isTableLine(lines[i].trim())){
          tableLines.push(lines[i].trim());
          i++;
        }
        i--;
        out.push(this.table(tableLines));
        continue;
      }
      const heading = line.match(/^(#{1,4})\s+(.+)$/);
      if(heading){
        flushList();
        out.push(`<h${heading[1].length}>${this.inline(heading[2])}</h${heading[1].length}>`);
        continue;
      }
      const bullet = line.match(/^[-*]\s+(.+)$/);
      const ordered = line.match(/^\d+[.)]\s+(.+)$/);
      if(bullet || ordered){
        const nextKind = bullet ? 'ul' : 'ol';
        if(list.length && listKind !== nextKind) flushList();
        listKind = nextKind;
        list.push(this.inline((bullet || ordered)[1]));
        continue;
      }
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
